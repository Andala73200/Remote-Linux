from __future__ import annotations


class PackageFavoriteStorageMixin:
    def favorite_packages(self, manager: str) -> set[str]:
        groups = self.settings.get("package_favorites", {})
        names = groups.get(str(manager).lower(), []) if isinstance(groups, dict) else []
        return {str(name) for name in names if str(name).strip()}

    def set_package_favorite(self, manager: str, package: str, enabled: bool) -> None:
        key, name = str(manager).lower(), str(package).strip()
        groups = self.settings.get("package_favorites")
        if not isinstance(groups, dict):
            groups = self.settings["package_favorites"] = {}
        names = {value.casefold(): value for value in self.favorite_packages(key)}
        if enabled and name:
            names[name.casefold()] = name
        else:
            names.pop(name.casefold(), None)
        groups[key] = sorted(names.values(), key=str.casefold)
        self.save()

    def package_favorites_only(self, manager: str) -> bool:
        values = self.settings.get("package_favorites_only", {})
        return (
            bool(values.get(str(manager).lower(), False))
            if isinstance(values, dict) else False
        )

    def set_package_favorites_only(self, manager: str, enabled: bool) -> None:
        values = self.settings.get("package_favorites_only")
        if not isinstance(values, dict):
            values = self.settings["package_favorites_only"] = {}
        values[str(manager).lower()] = bool(enabled)
        self.save()
