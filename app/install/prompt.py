"""Validated console prompts for ``diag install``.

Stdlib only. Every helper re-asks on invalid input instead of accepting a bad
value or raising out of the install run. ``Prompter`` is constructed by the
caller with explicit flags so unit tests can drive it without a TTY.
"""
from __future__ import annotations

import getpass
import re
from urllib.parse import urlparse

# Guard against an operator holding Enter on an un-satisfiable prompt.
_MAX_ATTEMPTS = 6

_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")


class PromptAborted(RuntimeError):
    """Raised when a required value cannot be resolved from input."""


class Prompter:
    """Console prompts with defaults, validation, and section headers.

    ``interactive=False`` returns defaults without reading stdin.
    ``accept_defaults=True`` also skips reading stdin but echoes each resolved
    value so the operator can see what was chosen.
    """

    def __init__(
        self,
        *,
        interactive: bool = True,
        accept_defaults: bool = False,
    ) -> None:
        self.interactive = interactive
        self.accept_defaults = accept_defaults
        self._section_index = 0

    # -- output helpers ---------------------------------------------------
    def section(self, title: str, *, total: int | None = None) -> None:
        self._section_index += 1
        counter = f"{self._section_index}/{total}" if total else str(self._section_index)
        print(f"\n[{counter}] {title}")
        print("-" * (len(title) + len(counter) + 4))

    def note(self, message: str) -> None:
        print(f"  {message}")

    def warn(self, message: str) -> None:
        print(f"  ! {message}")

    def summary(self, title: str, rows: list[tuple[str, str]]) -> None:
        print(f"\n{title}")
        print("-" * len(title))
        width = max((len(label) for label, _ in rows), default=0)
        for label, value in rows:
            print(f"  {label.ljust(width)}  {value or '(none)'}")

    # -- input helpers ----------------------------------------------------
    def text(
        self,
        label: str,
        *,
        default: str = "",
        allow_empty: bool = True,
        help_text: str = "",
    ) -> str:
        def _validate(raw: str) -> tuple[bool, str, str]:
            if not raw and not allow_empty:
                return False, "", "a value is required"
            return True, raw, ""

        return self._ask(label, default, _validate, help_text=help_text)

    def url(
        self,
        label: str,
        *,
        default: str = "",
        allow_empty: bool = False,
        help_text: str = "",
    ) -> str:
        def _validate(raw: str) -> tuple[bool, str, str]:
            if not raw:
                if allow_empty:
                    return True, "", ""
                return False, "", "a URL is required"
            normalized = normalize_url(raw)
            if normalized is None:
                return (
                    False,
                    "",
                    "not a valid http(s) URL (example: http://127.0.0.1:9090)",
                )
            return True, normalized, ""

        return self._ask(label, default, _validate, help_text=help_text)

    def port(self, label: str, *, default: int, help_text: str = "") -> int:
        def _validate(raw: str) -> tuple[bool, str, str]:
            if not raw.isdigit():
                return False, "", "must be a number between 1 and 65535"
            value = int(raw)
            if not 1 <= value <= 65535:
                return False, "", "must be between 1 and 65535"
            return True, str(value), ""

        return int(self._ask(label, str(default), _validate, help_text=help_text))

    def choice(
        self,
        label: str,
        choices: list[str],
        *,
        default: str,
        help_text: str = "",
    ) -> str:
        rendered = "/".join(choices)

        def _validate(raw: str) -> tuple[bool, str, str]:
            lowered = raw.strip().lower()
            if lowered in choices:
                return True, lowered, ""
            return False, "", f"choose one of: {rendered}"

        return self._ask(
            f"{label} [{rendered}]", default, _validate, help_text=help_text
        )

    def yes_no(self, label: str, *, default: bool, help_text: str = "") -> bool:
        rendered = "Y/n" if default else "y/N"

        def _validate(raw: str) -> tuple[bool, str, str]:
            lowered = raw.strip().lower()
            if lowered in ("y", "yes"):
                return True, "y", ""
            if lowered in ("n", "no"):
                return True, "n", ""
            return False, "", "answer y or n"

        answer = self._ask(
            f"{label} [{rendered}]",
            "y" if default else "n",
            _validate,
            help_text=help_text,
        )
        return answer == "y"

    def secret(
        self,
        label: str,
        *,
        allow_empty: bool = True,
        help_text: str = "",
    ) -> str:
        if not self.interactive or self.accept_defaults:
            return ""
        if help_text:
            self.note(help_text)
        for _ in range(_MAX_ATTEMPTS):
            try:
                value = getpass.getpass(f"{label}: ").strip()
            except EOFError:
                return ""
            if value or allow_empty:
                return value
            self.warn("a value is required")
        raise PromptAborted(f"no value provided for {label!r}")

    # -- internals --------------------------------------------------------
    def _ask(
        self,
        label: str,
        default: str,
        validate,
        *,
        help_text: str = "",
    ) -> str:
        if not self.interactive:
            ok, value, _ = validate(default)
            return value if ok else default
        if self.accept_defaults:
            ok, value, reason = validate(default)
            if not ok:
                raise PromptAborted(f"{label}: {reason} (default {default!r})")
            print(f"  {label}: {value or '(none)'}  [accepted default]")
            return value

        if help_text:
            self.note(help_text)
        suffix = f" [{default}]" if default else ""
        for _ in range(_MAX_ATTEMPTS):
            try:
                raw = input(f"{label}{suffix}: ").strip()
            except EOFError:
                raw = ""
            if not raw:
                raw = default
            ok, value, reason = validate(raw)
            if ok:
                return value
            self.warn(reason)
        raise PromptAborted(f"could not resolve a valid value for {label!r}")


def normalize_url(raw: str) -> str | None:
    """Return a normalized http(s) URL, or ``None`` when unusable.

    Bare ``host:port`` input is a common typo, so it is upgraded to ``http://``
    rather than rejected.
    """
    candidate = raw.strip()
    if not candidate or any(ch.isspace() for ch in candidate):
        return None
    if "://" not in candidate:
        candidate = f"http://{candidate}"
    parsed = urlparse(candidate)
    if parsed.scheme not in ("http", "https"):
        return None
    host = parsed.hostname
    if not host:
        return None
    # Bracketed IPv6 literals contain colons; validate everything else strictly.
    if ":" not in host and not _HOSTNAME_RE.match(host):
        return None
    try:
        parsed.port  # raises ValueError on a non-numeric port
    except ValueError:
        return None
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"


_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1", "0.0.0.0")


def container_rewrite(url: str) -> str | None:
    """Rewrite a loopback URL to one a container can reach, if applicable.

    The installer probes from the operator's host, so ``127.0.0.1`` resolves
    there but points at the agent container itself once the agent is running.
    """
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.hostname not in _LOCAL_HOSTS:
        return None
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://host.docker.internal{port}{parsed.path.rstrip('/')}"
