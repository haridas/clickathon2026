"""Report the local Langfuse Docker stack without exposing configuration secrets."""

import subprocess


def main() -> None:
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}\t{{.Image}}\t{{.Status}}"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise SystemExit(result.stderr.strip() or "Docker is unavailable.")
    lines = [line for line in result.stdout.splitlines() if "langfuse" in line.lower()]
    print("\n".join(lines) if lines else "No running Langfuse containers found.")


if __name__ == "__main__":
    main()
