"""The one place that decides what counts as a credential."""

import re

# Google OAuth tokens and codes, GitLab, GitHub, Slack, AWS, Anthropic and OpenAI keys, private keys.
# Case-insensitive because the original worklog.ps1 used PowerShell's -replace, which is.
_SECRET = re.compile(
    r'ya29\.[\w-]+|\b4/0A[\w-]+|1//0[\w-]{20,}|glpat-[\w.-]+|gh[pousr]_\w{20,}|xox[abprs]-[\w-]+'
    r'|AKIA[0-9A-Z]{16}|sk-(ant-)?[\w-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----',
    re.IGNORECASE,
)


def redact(text: str) -> str:
    """Returns `text` with credentials replaced by `[REDACTED]`."""
    return _SECRET.sub('[REDACTED]', text)
