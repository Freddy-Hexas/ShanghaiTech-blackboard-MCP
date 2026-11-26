"""Add this checkout's runtime to its local Codex configuration, preserving existing settings."""

import json
import shutil
import sys
import tomllib
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    config = root / "config.local.toml"
    if not config.is_file():
        raise SystemExit("Create config.local.toml first; see config.example.toml.")
    destination = root / ".codex" / "config.toml"
    old = destination.read_text(encoding="utf-8") if destination.exists() else ""
    parsed = tomllib.loads(old)
    name = "shanghaitech-blackboard"
    launch = {
        "command": sys.executable,
        "args": ["-m", "blackboard_mcp.server", "--config", str(config)],
        "cwd": str(root),
    }
    # TOML rejects JSON's surrogate-pair escapes for characters such as emoji.
    launch_toml = (
        f"command = {json.dumps(launch['command'], ensure_ascii=False)}\n"
        f"args = {json.dumps(launch['args'], ensure_ascii=False)}\n"
        f"cwd = {json.dumps(launch['cwd'], ensure_ascii=False)}\n"
    )
    if name in parsed.get("mcp_servers", {}):
        print("Project MCP entry already exists; kept the existing configuration.")
        existing = parsed["mcp_servers"][name]
        # A deliberately configured HTTP connection has no local launch paths.
        if "url" not in existing:
            changed = [key for key, value in launch.items() if existing.get(key) != value]
            if changed:
                print("Launch settings differ from this checkout: " + ", ".join(changed))
                print(
                    f"If you moved the project or recreated its virtual environment, replace "
                    f"command, args and cwd in [mcp_servers.{name}] in {destination} with:"
                )
                print(launch_toml, end="")
    else:
        entry = (
            f"\n[mcp_servers.{name}]\n"
            f"{launch_toml}"
            "startup_timeout_sec = 30\n"
            "tool_timeout_sec = 240\n"
        )
        tomllib.loads(old + entry)
        destination.parent.mkdir(exist_ok=True)
        destination.write_text(old + entry, encoding="utf-8")
        print(f"Configured project MCP: {destination}")
    # Materialize machine-specific launch paths outside the portable, tracked package.
    local_root = root / ".local"
    package = local_root / "plugins" / "blackboard"
    shutil.copytree(root / "plugins" / "blackboard", package, dirs_exist_ok=True)
    manifest_path = package / "mcp.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["mcpServers"][name].update(
        command=launch["command"],
        args=launch["args"],
    )
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    marketplace = json.loads(
        (root / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")
    )
    marketplace["name"] = "shanghaitech-local-runtime"
    catalog = local_root / ".agents" / "plugins" / "marketplace.json"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text(json.dumps(marketplace, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared local plugin marketplace: {local_root}")


if __name__ == "__main__":
    main()
