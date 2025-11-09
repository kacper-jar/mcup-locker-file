#!/usr/bin/env python3
"""
Automated version checker for Minecraft server types.
Checks various sources for new versions and creates individual PRs for each change.
"""

import os
import json
import requests
import subprocess
from datetime import datetime
from typing import Dict, List, Tuple


class VersionChecker:
    def __init__(self, locker_file: str = 'locker.json'):
        self.locker_file = locker_file
        self.locker_data = self.load_locker()
        self.github_token = os.environ.get('GITHUB_TOKEN')
        self.repo = os.environ.get('GITHUB_REPOSITORY', '')

    def load_locker(self) -> dict:
        """Load the current locker file."""
        with open(self.locker_file, 'r') as f:
            return json.load(f)

    def save_locker(self):
        """Save the updated locker file."""
        with open(self.locker_file, 'w') as f:
            json.dump(self.locker_data, f, indent=4)

    def get_existing_versions(self, server_type: str) -> Dict[str, dict]:
        """Get dict of existing versions for a server type."""
        if server_type not in self.locker_data['servers']:
            return {}
        return {entry['version']: entry for entry in self.locker_data['servers'][server_type]}

    def run_command(self, cmd: list) -> bool:
        """Run a shell command and return success status."""
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            return True
        except subprocess.CalledProcessError as e:
            print(f"Command failed: {' '.join(cmd)}")
            print(f"Error: {e.stderr}")
            return False

    def has_open_pr(self, title: str) -> bool:
        """Return True if there is already an open PR with the given title."""
        if not self.repo:
            return False

        headers = {"Accept": "application/vnd.github+json"}
        if self.github_token:
            headers["Authorization"] = f"token {self.github_token}"

        url = f"https://api.github.com/repos/{self.repo}/pulls?state=open&per_page=100"
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            for pr in resp.json():
                if pr.get("title") == title:
                    return True
        except Exception as e:
            print(f"Warning: failed to check existing PRs: {e}")
        return False

    def create_pr(self, server_type: str, version: str, is_new: bool, entry: dict):
        """Create a PR for a single version change."""
        self.run_command(['git', 'checkout', 'main'])
        self.run_command(['git', 'pull', 'origin', 'main'])

        action = "new" if is_new else "update"
        pr_title = f"New {server_type.capitalize()} {version}" if is_new else f"Update {server_type.capitalize()} {version}"

        if self.has_open_pr(pr_title):
            print(f"Skipping PR creation: an open PR already exists with title '{pr_title}'")
            return

        branch_name = f"auto-{action}-{server_type}-{version}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

        if not self.run_command(['git', 'checkout', '-b', branch_name]):
            return

        self.locker_data = self.load_locker()

        if server_type not in self.locker_data['servers']:
            self.locker_data['servers'][server_type] = []

        pr_label = None

        if is_new:
            self.locker_data['servers'][server_type].append(entry)
            commit_msg = f"locker: New {server_type.capitalize()} {version}"
            pr_label = "release"
            pr_body = f"""## New Version Available

**Server Type:** {server_type.capitalize()}  
**Version:** {version}  
**Source:** {entry['source']}

### Details
- Supports Plugins: {'Yes' if entry['supports_plugins'] else 'No'}
- Supports Mods: {'Yes' if entry['supports_mods'] else 'No'}
- Configs: {', '.join(entry['configs']) if entry['configs'] else 'None'}

### Download URL
```
{entry.get('server_url') or entry.get('installer_url', 'N/A')}
```

---
*This PR was automatically created by the version checker workflow.*
"""
        else:
            for i, existing in enumerate(self.locker_data['servers'][server_type]):
                if existing['version'] == version:
                    self.locker_data['servers'][server_type][i] = entry
                    break

            commit_msg = f"locker: Update {server_type.capitalize()} {version}"
            pr_label = "update"
            pr_body = f"""## Version Update

**Server Type:** {server_type.capitalize()}  
**Version:** {version}  
**Source:** {entry['source']}

### What Changed
Updated download URL or build information for this version.

### New URL
```
{entry.get('server_url') or entry.get('installer_url', 'N/A')}
```

---
*This PR was automatically created by the version checker workflow.*
"""

        self.save_locker()

        if not self.run_command(['git', 'add', self.locker_file]):
            return

        if not self.run_command(['git', 'commit', '-m', commit_msg]):
            return

        if not self.run_command(['git', 'push', '-u', 'origin', branch_name]):
            return

        pr_command = [
            'gh', 'pr', 'create',
            '--title', pr_title,
            '--body', pr_body,
            '--head', branch_name,
            '--base', 'main',
            '--label', pr_label
        ]

        if self.run_command(pr_command):
            print(f"Created PR: {pr_title}")
        else:
            print(f"Failed to create PR for {server_type} {version}")

    def check_vanilla(self) -> List[Tuple[str, str, bool, dict]]:
        """Check for new Vanilla Minecraft versions."""
        print("  Fetching version manifest from Mojang...")
        url = "https://launchermeta.mojang.com/mc/game/version_manifest.json"
        response = requests.get(url)
        data = response.json()

        existing_versions = self.get_existing_versions('vanilla')
        changes = []

        print(f"  Found {len(data['versions'])} total versions from Mojang")
        print(f"  Currently tracking {len(existing_versions)} versions in locker")

        release_count = 0
        for version_info in data['versions']:
            if version_info['type'] == 'release':
                release_count += 1
                version = version_info['id']

                version_detail = requests.get(version_info['url']).json()
                if 'server' not in version_detail.get('downloads', {}):
                    print(f"  WARNING: {version} has no server download available")
                    continue

                server_url = version_detail['downloads']['server']['url']

                entry = {
                    "version": version,
                    "source": "DOWNLOAD",
                    "server_url": server_url,
                    "supports_plugins": False,
                    "supports_mods": False,
                    "configs": [],
                    "cleanup": []
                }

                if version not in existing_versions:
                    changes.append(('vanilla', version, True, entry))
                    print(f"  NEW: {version}")
                elif existing_versions[version].get('server_url') != server_url:
                    changes.append(('vanilla', version, False, entry))
                    print(f"  UPDATE: {version} (URL changed)")

        print(f"  Found {release_count} release versions total")

        return changes

    def run(self):
        print("Checking for new Vanilla versions...\n")
        print("=" * 60)

        try:
            changes = self.check_vanilla()
            print("=" * 60)
            print()
        except Exception as e:
            print(f"Error checking Vanilla: {e}")
            return

        if not changes:
            print("No new versions or updates found")
            return

        print(f"\nFound {len(changes)} change(s)")
        print("=" * 60)

        for server_type, version, is_new, entry in changes:
            action = "NEW" if is_new else "UPDATE"
            print(f"\n[{action}] Processing {server_type} {version}...")
            try:
                self.create_pr(server_type, version, is_new, entry)
            except Exception as e:
                print(f"Failed to create PR: {e}")

        print(f"\n{'=' * 60}")
        print(f"Completed processing {len(changes)} change(s)")


if __name__ == '__main__':
    checker = VersionChecker()
    checker.run()