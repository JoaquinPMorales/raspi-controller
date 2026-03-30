#!/usr/bin/env python3
"""
Jellyfin Media Copy Script
Scans TV series and movies from qBittorrent downloads and copies them to Jellyfin.
Supports two modes:
1. Internal: Move files within the Raspberry Pi (downloads → Jellyfin library)
2. External: Copy files from Raspberry Pi to local laptop
"""

import os
import sys
import yaml
import signal
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

import inquirer
from scanner import FolderScanner
from copier import RsyncCopier, ExternalCopier
from jellyfin import refresh_jellyfin_library

console = Console()


def load_config(config_path: str = "config.yaml") -> dict:
    """Load configuration from YAML file."""
    if not os.path.exists(config_path):
        console.print(f"[red]Error: Config file not found: {config_path}[/red]")
        console.print("[yellow]Please copy config.yaml.example to config.yaml and configure it.[/yellow]")
        sys.exit(1)
    
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def validate_config(config: dict) -> bool:
    """Validate configuration values."""
    required = ['pi', 'paths', 'options']
    for key in required:
        if key not in config:
            console.print(f"[red]Error: Missing required config section: {key}[/red]")
            return False
    
    pi_config = config['pi']
    if not pi_config.get('host') or not pi_config.get('user'):
        console.print("[red]Error: pi.host and pi.user are required[/red]")
        return False
    
    if not pi_config.get('password') and not pi_config.get('key_path'):
        console.print("[red]Error: Either pi.password or pi.key_path must be set[/red]")
        return False
    
    return True


def organize_items(items: list) -> tuple:
    """Organize items by content type (TV shows and movies)."""
    tv_shows = {}
    movies = {}
    
    for item in items:
        content_type = item.get('content_type', 'movie')
        show_name = item.get('show', item['name'])
        
        if content_type == 'tv':
            season = item.get('season', 'Unknown')
            
            if show_name not in tv_shows:
                tv_shows[show_name] = {}
            
            if season not in tv_shows[show_name]:
                tv_shows[show_name][season] = []
            
            tv_shows[show_name][season].append(item)
        else:
            # Movies are organized by movie name
            if show_name not in movies:
                movies[show_name] = []
            movies[show_name].append(item)
    
    return tv_shows, movies


def display_content(tv_shows: dict, movies: dict) -> None:
    """Display discovered content in a formatted display."""
    if tv_shows:
        console.print(Panel.fit("[bold cyan]TV Shows[/bold cyan]"))
        
        for show_name in sorted(tv_shows.keys()):
            seasons = tv_shows[show_name]
            season_count = len(seasons)
            episode_count = sum(len(seasons[s]) for s in seasons)
            
            console.print(f"\n[bold green]{show_name}[/bold green]")
            console.print(f"  Seasons: {season_count} | Items: {episode_count}")
            
            for season in sorted(seasons.keys(), key=lambda x: str(x)):
                items = seasons[season]
                console.print(f"    [yellow]Season {season}[/yellow]: {len(items)} item(s)")
    
    if movies:
        console.print(Panel.fit("[bold magenta]Movies[/bold magenta]"))
        
        for movie_name in sorted(movies.keys()):
            items = movies[movie_name]
            console.print(f"[bold green]{movie_name}[/bold green] - {len(items)} item(s)")
    
    if not tv_shows and not movies:
        console.print("[yellow]No content found in downloads directory.[/yellow]")


def select_content(tv_shows: dict, movies: dict) -> list:
    """Interactive selection of content to copy."""
    choices = []
    
    # Add TV shows
    for show_name in sorted(tv_shows.keys()):
        seasons = tv_shows[show_name]
        for season in sorted(seasons.keys(), key=lambda x: str(x)):
            items = seasons[season]
            path = items[0]['path']
            display = f"[TV] {show_name} - Season {season} ({len(items)} items)"
            choices.append((display, {
                'show': show_name, 
                'season': season, 
                'path': path, 
                'items': items,
                'content_type': 'tv'
            }))
    
    # Add movies
    for movie_name in sorted(movies.keys()):
        items = movies[movie_name]
        path = items[0]['path']
        display = f"[Movie] {movie_name} ({len(items)} items)"
        choices.append((display, {
            'show': movie_name,
            'season': None,
            'path': path,
            'items': items,
            'content_type': 'movie'
        }))
    
    if not choices:
        console.print("[yellow]No content found in downloads directory.[/yellow]")
        return []
    
    questions = [
        inquirer.Checkbox('selected',
                         message="Select content to copy (space to select, enter to confirm)",
                         choices=choices,
                         carousel=True)
    ]
    
    try:
        answers = inquirer.prompt(questions)
        if not answers or not answers['selected']:
            console.print("[yellow]No content selected. Exiting.[/yellow]")
            return []
        return answers['selected']
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled by user.[/yellow]")
        return []


def select_mode() -> str:
    """Ask user to select operation mode."""
    questions = [
        inquirer.List('mode',
                     message="Select operation mode",
                     choices=[
                         ('Internal - Move files within Pi (downloads → Jellyfin)', 'internal'),
                         ('External - Copy files from Pi to this laptop', 'external'),
                     ],
                     default='internal')
    ]
    
    try:
        answers = inquirer.prompt(questions)
        return answers['mode'] if answers else 'internal'
    except KeyboardInterrupt:
        return 'internal'


def confirm_operation(selected: list, dry_run: bool, mode: str) -> bool:
    """Confirm the copy operation with the user."""
    total_items = sum(len(s['items']) for s in selected)
    tv_count = sum(1 for s in selected if s['content_type'] == 'tv')
    movie_count = sum(1 for s in selected if s['content_type'] == 'movie')
    
    mode_str = "INTERNAL (Pi → Pi)" if mode == 'internal' else "EXTERNAL (Pi → Laptop)"
    
    if dry_run:
        mode_text = f"[bold yellow]DRY RUN MODE - {mode_str}[/bold yellow]"
    else:
        mode_text = f"[bold red]LIVE MODE - {mode_str}[/bold red]"
    
    console.print(Panel.fit(
        f"{mode_text}\n"
        f"Selected: {len(selected)} item(s)\n"
        f"  TV Shows: {tv_count}\n"
        f"  Movies: {movie_count}\n"
        f"Total files: {total_items}",
        title="Confirm Operation"
    ))
    
    if dry_run:
        console.print("[yellow]This will show what would be copied without actually copying.[/yellow]")
    else:
        if mode == 'internal':
            console.print("[red]This will COPY files within the Pi to your Jellyfin library.[/red]")
        else:
            console.print("[red]This will COPY files from Pi to your local laptop.[/red]")
    
    try:
        confirm = inquirer.prompt([
            inquirer.Confirm('proceed', message="Proceed?", default=True)
        ])
        return confirm and confirm['proceed']
    except KeyboardInterrupt:
        return False


def main():
    """Main entry point."""
    console.print(Panel.fit(
        "[bold blue]Jellyfin Media Copy[/bold blue]\n"
        "Copy TV series and movies from qBittorrent to Jellyfin or local machine",
        title="Welcome"
    ))
    
    # Select operation mode
    mode = select_mode()
    console.print(f"\n[blue]Mode selected: {mode.upper()}[/blue]")
    
    # Load configuration
    config = load_config()
    if not validate_config(config):
        sys.exit(1)
    
    # Connect to Raspberry Pi
    console.print("\n[blue]Connecting to Raspberry Pi...[/blue]")
    scanner = FolderScanner(config['pi'])
    
    if not scanner.connect():
        console.print("[red]Failed to connect to Raspberry Pi. Check your configuration.[/red]")
        sys.exit(1)
    
    console.print("[green]Connected successfully![/green]")
    
    # Scan downloads folder
    downloads_path = config['paths']['downloads']
    console.print(f"\n[blue]Scanning downloads folder: {downloads_path}[/blue]")
    
    items = scanner.scan_folder(downloads_path)
    
    if not items:
        console.print("[yellow]No items found in downloads folder.[/yellow]")
        scanner.close()
        sys.exit(0)
    
    console.print(f"[green]Found {len(items)} item(s)[/green]")
    
    # Organize and display content
    tv_shows, movies = organize_items(items)
    display_content(tv_shows, movies)
    
    # Let user select content
    selected = select_content(tv_shows, movies)
    if not selected:
        scanner.close()
        sys.exit(0)
    
    # Confirm operation
    dry_run = config['options'].get('dry_run', False)
    if not confirm_operation(selected, dry_run, mode):
        console.print("[yellow]Operation cancelled.[/yellow]")
        scanner.close()
        sys.exit(0)
    
    # Perform copy based on mode
    if mode == 'internal':
        copier = RsyncCopier(config['pi'], config['paths'], config['options'])
    else:
        # External mode - copy to local laptop
        local_paths = {
            'local_destination': config['paths'].get('local_destination', './downloads')
        }
        copier = ExternalCopier(config['pi'], local_paths, config['options'])
    
    def signal_handler(sig, frame):
        console.print("\n[yellow]Interrupted by user. Cleaning up...[/yellow]")
        copier.cancel()
        scanner.close()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        success = copier.copy_items(selected, console)
        if success:
            console.print("\n[green bold]All operations completed successfully![/green bold]")
            
            # Refresh Jellyfin library if internal mode and not dry_run
            if mode == 'internal' and not dry_run:
                console.print("\n[blue]Refreshing Jellyfin library...[/blue]")
                jellyfin_config = config.get('jellyfin', {})
                refresh_success = refresh_jellyfin_library(
                    host=jellyfin_config.get('host', config['pi']['host']),
                    port=jellyfin_config.get('port', 8096),
                    api_key=jellyfin_config.get('api_key'),
                    scanner=scanner
                )
                if refresh_success:
                    console.print("[green]Jellyfin library refresh triggered successfully![/green]")
                else:
                    console.print("[yellow]Could not refresh Jellyfin library automatically.[/yellow]")
                    console.print("[dim]You may need to refresh manually in Jellyfin web interface.[/dim]")
        else:
            console.print("\n[red bold]Some operations failed. Check the output above.[/red bold]")
    except Exception as e:
        console.print(f"\n[red]Error during copy: {e}[/red]")
    finally:
        scanner.close()


if __name__ == "__main__":
    main()
