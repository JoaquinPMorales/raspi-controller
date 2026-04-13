"""
System updater module for Raspberry Pi maintenance.
"""

import os
from typing import Optional
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
import paramiko


class SystemUpdater:
    """Handles system updates and Flatpak updates on Raspberry Pi via SSH."""
    
    def __init__(self, pi_config: dict):
        self.pi_config = pi_config
        self.ssh = None
        self.cancelled = False
        self.sudo_password = pi_config.get('sudo_password', '')
    
    def cancel(self):
        """Signal cancellation of ongoing operations."""
        self.cancelled = True
    
    def _connect(self) -> bool:
        """Establish SSH connection."""
        try:
            self.ssh = paramiko.SSHClient()
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            connect_kwargs = {
                'hostname': self.pi_config['host'],
                'port': self.pi_config.get('port', 22),
                'username': self.pi_config['user'],
                'timeout': 10,
            }
            
            key_path = self.pi_config.get('key_path')
            if key_path:
                key_path = os.path.expanduser(key_path)
                if os.path.exists(key_path):
                    connect_kwargs['key_filename'] = key_path
            
            password = self.pi_config.get('password')
            if password:
                connect_kwargs['password'] = password
            
            self.ssh.connect(**connect_kwargs)
            return True
            
        except Exception as e:
            print(f"SSH connection failed: {e}")
            return False
    
    def _run_sudo_command(self, command: str, console: Console, description: str) -> tuple:
        """Run a sudo command with password via stdin."""
        try:
            # Use sudo -S to read password from stdin
            full_command = f'echo "{self.sudo_password}" | sudo -S {command}'
            stdin, stdout, stderr = self.ssh.exec_command(full_command, get_pty=True)
            
            # Stream output
            while not self.cancelled:
                line = stdout.readline()
                if not line:
                    break
                console.print(f"[dim]{line.rstrip()}[/dim]")
            
            exit_status = stdout.channel.recv_exit_status()
            error = stderr.read().decode('utf-8', errors='ignore').strip()
            
            return exit_status == 0, error
            
        except Exception as e:
            return False, str(e)

    async def _run_sudo_command_async(self, command: str, console: Console, description: str) -> tuple:
        """Async variant using async_helpers to avoid blocking the event loop."""
        try:
            try:
                from . import async_helpers
            except Exception:
                import async_helpers

            full_command = f'echo "{self.sudo_password}" | sudo -S {command}'
            rc, stdout, stderr = await async_helpers.async_paramiko_exec(self.ssh, full_command)
            if stdout:
                for line in stdout.splitlines():
                    console.print(f"[dim]{line.rstrip()}[/dim]")
            error = (stderr or "").strip()
            return rc == 0, error
        except Exception as e:
            return False, str(e)
    
    def _run_command(self, command: str, console: Console, description: str) -> tuple:
        """Run a command and stream output to console."""
        try:
            stdin, stdout, stderr = self.ssh.exec_command(command, get_pty=True)
            
            # Stream output
            while not self.cancelled:
                line = stdout.readline()
                if not line:
                    break
                console.print(f"[dim]{line.rstrip()}[/dim]")
            
            exit_status = stdout.channel.recv_exit_status()
            error = stderr.read().decode('utf-8', errors='ignore').strip()
            
            return exit_status == 0, error
            
        except Exception as e:
            return False, str(e)
    
    def update_system(self, console: Console, dry_run: bool = False) -> bool:
        """Update system packages."""
        if dry_run:
            console.print("[yellow][DRY RUN] Would update system packages[/yellow]")
            return True
        
        console.print("\n[bold blue]Updating system packages...[/bold blue]")
        
        # Update package list
        console.print("[dim]Running: sudo apt update[/dim]")
        success, error = self._run_sudo_command(
            'apt update -y',
            console,
            "Updating package list"
        )
        
        if not success:
            console.print(f"[red]Failed to update package list: {error}[/red]")
            console.print("[yellow]Note: Maintenance mode requires sudo_password in config.yaml[/yellow]")
            return False
        
        if self.cancelled:
            return False
        
        # Upgrade packages
        console.print("[dim]Running: sudo apt upgrade -y[/dim]")
        success, error = self._run_sudo_command(
            'apt upgrade -y',
            console,
            "Upgrading packages"
        )
        
        if not success:
            console.print(f"[yellow]Package upgrade may have issues: {error}[/yellow]")
            # Don't fail here - some packages may fail but overall success
        
        if self.cancelled:
            return False
        
        # Autoremove old packages
        console.print("[dim]Running: sudo apt autoremove -y[/dim]")
        self._run_sudo_command(
            'apt autoremove -y',
            console,
            "Removing old packages"
        )
        
        console.print("[green]✓ System update completed[/green]")
        return True
    
    def update_flatpak(self, console: Console, dry_run: bool = False) -> bool:
        """Update Flatpak applications."""
        if dry_run:
            console.print("[yellow][DRY RUN] Would update Flatpak applications[/yellow]")
            return True
        
        console.print("\n[bold blue]Updating Flatpak applications...[/bold blue]")
        
        # Check if flatpak is installed
        success, _ = self._run_command('which flatpak', console, "Checking Flatpak")
        if not success:
            console.print("[yellow]Flatpak is not installed on this system[/yellow]")
            return True  # Not a failure, just not installed
        
        if self.cancelled:
            return False
        
        # Update flatpak apps
        console.print("[dim]Running: sudo flatpak update -y[/dim]")
        success, error = self._run_sudo_command(
            'flatpak update -y',
            console,
            "Updating Flatpak apps"
        )
        
        if not success:
            console.print(f"[yellow]Flatpak update may have issues: {error}[/yellow]")
        
        console.print("[green]✓ Flatpak update completed[/green]")
        return True
    
    def perform_updates(self, console: Console, dry_run: bool = False) -> bool:
        """Perform all updates (system and flatpak)."""
        if not self._connect():
            console.print("[red]Failed to connect to Pi for updates.[/red]")
            return False
        
        try:
            if dry_run:
                console.print("\n[bold yellow]DRY RUN MODE[/bold yellow]")
                console.print("The following would be executed:\n")
            else:
                console.print("\n[bold red]LIVE MODE - System updates will be applied[/bold red]")
                console.print("[dim]This may take several minutes depending on update size...[/dim]\n")
            
            # System updates
            system_success = self.update_system(console, dry_run)
            if self.cancelled:
                return False
            
            # Flatpak updates
            flatpak_success = self.update_flatpak(console, dry_run)
            if self.cancelled:
                return False
            
            if dry_run:
                console.print("\n[green]Dry run completed. No changes were made.[/green]")
            else:
                console.print("\n[bold green]All updates completed successfully![/bold green]")
            
            return system_success and flatpak_success
            
        except Exception as e:
            console.print(f"[red]Error during updates: {e}[/red]")
            return False
        finally:
            if self.ssh:
                self.ssh.close()
