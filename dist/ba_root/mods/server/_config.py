"""config storage core."""

from __future__ import annotations

import re

from .enums import Playlist, Utility
from .storage import Storage


class ConfigDict(dict):
    """for dot notation (ah i love easy things..)"""

    def __getitem__(self, key: str):
        item = super().__getitem__(key)
        if isinstance(item, dict) and not isinstance(item, ConfigDict):
            item = ConfigDict(item)
            self[key] = item
        return item

    def __getattr__(self, setting: str):
        try:
            return self[setting]
        except KeyError:
            raise AttributeError(f"Config has no setting: {setting}")


class Config(Storage):
    """config storage class."""

    def __init__(self) -> None:
        super().__init__("config.json")
        # our mods config file should stay in the root of the project folder.
        project_root_dir = self.directory.parents[2]
        self.path = project_root_dir / "mods_config.json"
        self.toml = project_root_dir / "config.toml"
        self.template_file = (
            self.directory.parents[1]
            / "ba_data"
            / "python"
            / "mods_config_template.json"
        )

    def bootstrap(self) -> None:
        """creates essential files."""
        template = self.read(self.template_file)
        if not self.path.exists():
            # mhm.. we take it from template file.
            self.commit(template)
            return

        # add the settings that were introduced after the file was created.
        config = self.read()
        if self._merge_missing(config, template):
            self.commit(config)

    @staticmethod
    def _merge_missing(config: dict, template: dict) -> bool:
        """adds the keys of template missing in config (recursively), returns whether it changed."""
        changed = False
        for key, value in template.items():
            if key not in config:
                config[key] = value
                changed = True
            elif isinstance(value, dict) and isinstance(config[key], dict):
                changed |= Config._merge_missing(config[key], value)
        return changed

    def toggle(self, utility: Utility) -> bool:
        """toggles the utility."""
        config = self.read()
        value = config.get(utility)

        # handle for nested utility settings
        if isinstance(value, dict):
            # its a dict.
            new_state = not value["enable"]
            value["enable"] = new_state
            self.commit(config)
            return new_state

        new_state = not value
        config[utility] = new_state
        self.commit(config)
        return new_state

    def set_playlist(self, playlist: Playlist) -> None:
        """changes the playlist code in .toml file."""
        code = playlist.value
        content = self.toml.read_text(encoding="utf-8")
        # match with regex.
        pattern = r"^(playlist_code\s*=\s*)[^\r\n#]+"
        new_content = re.sub(pattern, rf"\g<1>{code}", content, flags=re.MULTILINE)
        # store to temp file first.
        temp_file = self.toml.with_suffix(".toml.tmp")
        temp_file.write_text(new_content, encoding="utf-8")
        # replace with oriignal safely.
        temp_file.replace(self.toml)

    def __getattr__(self, key: str):
        data = ConfigDict(self.read())
        if key in data:
            return data[key]
        raise AttributeError(f"Config has no setting: {key}")


config = Config()
