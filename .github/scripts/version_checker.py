#!/usr/bin/env python3
"""
Automated version checker for Minecraft server types.
Checks various sources for new versions and creates individual PRs for each change.
"""

import os
import json
import logging
import requests
import subprocess
from datetime import datetime
from typing import Dict, List, Tuple
import xml.etree.ElementTree as ET


class VersionChecker:
    def __init__(self, locker_file: str = 'locker.json'):
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

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
            logging.error(f"Command failed: {' '.join(cmd)}")
            logging.error(f"Error: {e.stderr}")
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
            logging.warning(f"Failed to check existing PRs: {e}")
        return False

    def close_outdated_pr(self, server_type: str, version: str) -> str:
        """
        Close any existing open PR for the same server_type and version but different content.
        Returns a message string to append to the new PR body if one was closed.
        """
        if not self.repo:
            return ""

        headers = {"Accept": "application/vnd.github+json"}
        if self.github_token:
            headers["Authorization"] = f"token {self.github_token}"

        url = f"https://api.github.com/repos/{self.repo}/pulls?state=open&per_page=100"
        closed_pr_msg = ""
        
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            
            target_title_start = f"Update {server_type.capitalize()} {version}"
            
            for pr in resp.json():
                title = pr.get("title", "")
                if title.startswith(target_title_start):
                    pr_number = pr.get("number")
                    logging.info(f"Closing outdated PR #{pr_number}: {title}")
                    
                    close_url = f"https://api.github.com/repos/{self.repo}/pulls/{pr_number}"
                    requests.patch(close_url, headers=headers, json={"state": "closed"}, timeout=15)
                    
                    closed_pr_msg += f"\nCloses #{pr_number}"
                    
        except Exception as e:
            logging.warning(f"Failed to close outdated PRs: {e}")
            
        return closed_pr_msg

    def create_pr(self, server_type: str, version: str, is_new: bool, entry: dict):
        """Create a PR for a single version change."""
        self.run_command(['git', 'checkout', 'main'])
        self.run_command(['git', 'pull', 'origin', 'main'])

        action = "new" if is_new else "update"
        pr_title = f"Add {server_type.capitalize()} {version}" if is_new else f"Update {server_type.capitalize()} {version}"

        if self.has_open_pr(pr_title):
            logging.info(f"Skipping PR creation: an open PR already exists with title '{pr_title}'")
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
            commit_msg = f"locker: Add {server_type.capitalize()} {version}"
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
            closed_note = self.close_outdated_pr(server_type, version)
            if closed_note:
                pr_body += f"\n\n{closed_note}"

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
            logging.info(f"Created PR: {pr_title}")
        else:
            logging.error(f"Failed to create PR for {server_type} {version}")

    def check_vanilla(self) -> List[Tuple[str, str, bool, dict]]:
        """Check for new Vanilla Minecraft versions."""
        logging.info("Fetching version manifest from Mojang...")
        url = "https://launchermeta.mojang.com/mc/game/version_manifest.json"
        response = requests.get(url)
        data = response.json()

        existing_versions = self.get_existing_versions('vanilla')
        changes = []

        logging.info(f"Found {len(data['versions'])} total versions from Mojang")
        logging.info(f"Currently tracking {len(existing_versions)} versions in locker")

        release_count = 0
        for version_info in data['versions']:
            if version_info['type'] == 'release':
                release_count += 1
                version = version_info['id']

                version_detail = requests.get(version_info['url']).json()
                if 'server' not in version_detail.get('downloads', {}):
                    logging.warning(f"{version} has no server download available")
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
                    logging.info(f"NEW: {version}")
                elif existing_versions[version].get('server_url') != server_url:
                    changes.append(('vanilla', version, False, entry))
                    logging.info(f"UPDATE: {version} (URL changed)")

        logging.info(f"Found {release_count} release versions total")

        return changes

    def check_paper(self) -> List[Tuple[str, str, bool, dict]]:
        """Check for new PaperMC versions."""
        logging.info("Fetching version manifest from PaperMC...")
        url = "https://api.papermc.io/v2/projects/paper"
        try:
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            logging.error(f"Error fetching Paper manifest: {e}")
            return []

        existing_versions = self.get_existing_versions('paper')
        changes = []

        versions = data.get('versions', [])
        logging.info(f"Found {len(versions)} total versions from PaperMC")
        logging.info(f"Currently tracking {len(existing_versions)} versions in locker")

        for version in versions:
            if '-' in version:
                continue

            builds_url = f"https://api.papermc.io/v2/projects/paper/versions/{version}/builds"
            try:
                build_resp = requests.get(builds_url, timeout=15)
                build_resp.raise_for_status()
                build_data = build_resp.json()
            except Exception as e:
                logging.error(f"Error fetching builds for {version}: {e}")
                continue

            builds = build_data.get('builds', [])
            if not builds:
                logging.warning(f"{version} has no builds available")
                continue
            
            stable_builds = [b for b in builds if b.get('channel') == 'default']
            
            if not stable_builds:
                continue

            latest_build = stable_builds[-1]
            build_number = latest_build.get('build')
            downloads = latest_build.get('downloads', {})
            app_download = downloads.get('application', {})
            
            if not app_download:
                logging.warning(f"{version} build {build_number} has no application download")
                continue

            file_name = app_download.get('name')
            server_url = f"https://api.papermc.io/v2/projects/paper/versions/{version}/builds/{build_number}/downloads/{file_name}"

            configs = []
            try:
                parts = [int(p) for p in version.split('.')]

                if parts[0] == 1 and parts[1] >= 19:
                     configs = ['bukkit', 'spigot', 'paper-global', 'paper-world-defaults']
                else:
                    configs = ['bukkit', 'spigot', 'paper']
            except ValueError:
                logging.warning(f"Could not parse version {version} for config determination, defaulting to old configs")
                configs = ['bukkit', 'spigot', 'paper']

            entry = {
                "version": version,
                "source": "DOWNLOAD",
                "server_url": server_url,
                "supports_plugins": True,
                "supports_mods": False,
                "configs": configs,
                "cleanup": []
            }

            if version not in existing_versions:
                changes.append(('paper', version, True, entry))
                logging.info(f"NEW: {version}")
            elif existing_versions[version].get('server_url') != server_url:
                changes.append(('paper', version, False, entry))
                logging.info(f"UPDATE: {version} (New build {build_number})")

        return changes

    def check_forge(self) -> List[Tuple[str, str, bool, dict]]:
        """Check for new Forge versions."""
        logging.info("Fetching promotions from Forge...")
        url = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
        try:
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            logging.error(f"Error fetching Forge promotions: {e}")
            return []

        existing_versions = self.get_existing_versions('forge')
        changes = []
        promos = data.get('promos', {})
        
        logging.info(f"Found {len(promos)} total promo entries from Forge")
        logging.info(f"Currently tracking {len(existing_versions)} versions in locker")

        for key, forge_version in promos.items():
            if not key.endswith('-latest'):
                continue
                
            mc_version = key.replace('-latest', '')
            
            try:
                mc_version_parts = [int(x) for x in mc_version.split('.')]
            except ValueError:
                logging.warning(f"Skipping weird version: {mc_version}")
                continue

            if mc_version_parts < [1, 5, 2]:
                logging.info(f"Skipping Forge for MC {mc_version} (too old - not supported by mcup / no installer download)")
                continue
            
            is_legacy_url = ([1, 7, 10] <= mc_version_parts <= [1, 10, 0] and mc_version_parts not in [[1, 8], [1, 8, 8]]) or mc_version_parts == [1, 7, 2]
            
            if is_legacy_url:
                 # e.g. 1.10-12.18.0.2000 -> forge-1.10-12.18.0.2000-1.10.0-installer.jar
                 if mc_version_parts == [1, 7, 2]:
                     mc_version_full = "mc172"
                 else:
                     mc_version_full = mc_version if len(mc_version_parts) == 3 else f"{mc_version}.0"
                 base_url = f"https://maven.minecraftforge.net/net/minecraftforge/forge/{mc_version}-{forge_version}-{mc_version_full}/"
                 file_name = f"forge-{mc_version}-{forge_version}-{mc_version_full}-installer.jar"
            else:
                 # e.g. 1.20.1-47.4.13 -> forge-1.20.1-47.4.13-installer.jar
                 base_url = f"https://maven.minecraftforge.net/net/minecraftforge/forge/{mc_version}-{forge_version}/"
                 file_name = f"forge-{mc_version}-{forge_version}-installer.jar"

            installer_url = f"{base_url}{file_name}"
            
            cleanup = [file_name]
            
            is_modern_cleanup = False
            if mc_version_parts[0] > 1: 
                is_modern_cleanup = True
            elif mc_version_parts[0] == 1 and mc_version_parts[1] >= 17:
                is_modern_cleanup = True
            
            if is_modern_cleanup:
                cleanup.extend([
                    "user_jvm_args.txt",
                    "run.bat",
                    "run.sh"
                ])

            entry = {
                "version": mc_version,
                "source": "INSTALLER",
                "installer_url": installer_url,
                "installer_args": [
                    "java",
                    "-jar",
                    "%file_path",
                    "--installServer"
                ],
                "supports_plugins": False,
                "supports_mods": True,
                "configs": [],
                "cleanup": cleanup
            }

            if mc_version not in existing_versions:
                changes.append(('forge', mc_version, True, entry))
                logging.info(f"NEW: {mc_version} (Forge {forge_version})")
            elif existing_versions[mc_version].get('installer_url') != installer_url:
                changes.append(('forge', mc_version, False, entry))
                logging.info(f"UPDATE: {mc_version} (Forge {forge_version})")

        return changes

    def check_neoforge(self) -> List[Tuple[str, str, bool, dict]]:
        """Check for new NeoForge versions."""
        logging.info("Fetching metadata from NeoForge...")
        url = "https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml"
        try:
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            root = ET.fromstring(response.content)
        except Exception as e:
            logging.error(f"Error fetching NeoForge metadata: {e}")
            return []

        existing_versions = self.get_existing_versions('neoforge')
        changes = []
        
        versioning = root.find('versioning')
        if versioning is None:
            logging.warning("Could not find versioning tag in NeoForge metadata")
            return []
            
        versions_tag = versioning.find('versions')
        if versions_tag is None:
            logging.warning("Could not find versions tag in NeoForge metadata")
            return []

        all_versions = [v.text for v in versions_tag.findall('version')]
        logging.info(f"Found {len(all_versions)} total versions from NeoForge")
        logging.info(f"Currently tracking {len(existing_versions)} versions in locker")

        latest_versions: Dict[str, str] = {}

        for neo_version in all_versions:
            if neo_version.endswith('-beta'):
                continue
            
            try:
                parts = [int(p) for p in neo_version.split('.')]
            except ValueError:
                logging.warning(f"Skipping unparseable NeoForge version: {neo_version}")
                continue
            
            major = parts[0]
            minor = parts[1]
            patch = parts[2] if len(parts) > 2 else 0
            
            mc_version = None
            if major >= 20: 
                mc_major = 1
                mc_minor = major
                mc_patch = minor
                
                if mc_patch == 0:
                    mc_version = f"{mc_major}.{mc_minor}"
                else:
                    mc_version = f"{mc_major}.{mc_minor}.{mc_patch}"
            
            if not mc_version:
                logging.warning(f"Skipping unknown NeoForge version format: {neo_version}")
                continue

            if mc_version not in latest_versions:
                latest_versions[mc_version] = neo_version
            else:
                current_latest = latest_versions[mc_version]
                try:
                    curr_parts = [int(p) for p in current_latest.split('.')]
                    new_parts = [int(p) for p in neo_version.split('.')]
                    
                    if new_parts > curr_parts:
                        latest_versions[mc_version] = neo_version
                except ValueError:
                    pass

        for mc_version, neo_version in latest_versions.items():
            installer_url = f"https://maven.neoforged.net/releases/net/neoforged/neoforge/{neo_version}/neoforge-{neo_version}-installer.jar"
            
            entry = {
                "version": mc_version,
                "source": "INSTALLER",
                "installer_url": installer_url,
                "installer_args": [
                    "java",
                    "-jar",
                    "%file_path",
                    "--installServer"
                ],
                "supports_plugins": False,
                "supports_mods": True,
                "configs": [],
                "cleanup": [
                    f"neoforge-{neo_version}-installer.jar",
                    "user_jvm_args.txt",
                    "run.bat",
                    "run.sh"
                ]
            }

            if mc_version not in existing_versions:
                changes.append(('neoforge', mc_version, True, entry))
                logging.info(f"NEW: {mc_version} (NeoForge {neo_version})")
            elif existing_versions[mc_version].get('installer_url') != installer_url:
                changes.append(('neoforge', mc_version, False, entry))
                logging.info(f"UPDATE: {mc_version} (NeoForge {neo_version})")

        return changes

    def is_url_valid(self, url: str) -> bool:
        """Check if a URL is valid."""
        if not url:
            return False

        try:
            response = requests.head(url, allow_redirects=True, timeout=10)
            if response.status_code == 404:
                return False
            if response.status_code in [405, 403]:
                pass
            else:
                return True
        except requests.RequestException:
            pass

        try:
            with requests.get(url, stream=True, allow_redirects=True, timeout=10) as response:
                if response.status_code == 404:
                    return False
                return True
        except requests.RequestException as e:
            logging.warning(f"Error checking URL {url}: {e}")
            return False

    def run(self):
        logging.info("Checking for new versions...")

        try:
            changes = []
            changes.extend(self.check_vanilla())
            changes.extend(self.check_paper())
            changes.extend(self.check_forge())
            changes.extend(self.check_neoforge())
        except Exception:
            logging.exception("Error checking versions")
            return

        if not changes:
            logging.info("No new versions or updates found")
            return

        logging.info(f"Found {len(changes)} change(s)")

        for server_type, version, is_new, entry in changes:
            action = "NEW" if is_new else "UPDATE"
            logging.info(f"[{action}] Processing {server_type} {version}...")
            
            check_url = entry.get('server_url') or entry.get('installer_url')
            if check_url:
                logging.info(f"Verifying URL: {check_url}")
                if not self.is_url_valid(check_url):
                    logging.warning(f"Skipping {server_type} {version}: URL returned 404 or is invalid.")
                    continue
            else:
                logging.warning(f"Skipping {server_type} {version}: No URL found to check.")
                continue

            try:
                self.create_pr(server_type, version, is_new, entry)
            except Exception as e:
                logging.error(f"Failed to create PR: {e}")

        logging.info(f"Completed processing {len(changes)} change(s)")


if __name__ == '__main__':
    checker = VersionChecker()
    checker.run()
