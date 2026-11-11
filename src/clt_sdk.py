"""Supply real Command Line Tools SDK metadata to Chromium's macOS build."""
import pathlib
import plistlib
import subprocess
import sys

def output(*args):
    return subprocess.check_output(args, text=True).strip()

developer = pathlib.Path(output("xcode-select", "-print-path"))
sdk = pathlib.Path(output("xcrun", "--sdk", "macosx", "--show-sdk-path"))
version = output("xcrun", "--sdk", "macosx", "--show-sdk-version")
with (sdk / "System/Library/CoreServices/SystemVersion.plist").open("rb") as f:
    build = plistlib.load(f)["ProductBuildVersion"]
package = plistlib.loads(subprocess.check_output(
    ["pkgutil", "--pkg-info-plist", "com.apple.pkg.CLTools_Executables"]))
parts = package["pkg-version"].split(".")
# These upstream fields are used for toolchain feature gates. Report the
# installed CLT release, rather than inventing an installed Xcode release.
tool_version = f"{int(parts[0]):02}{int(parts[1])}{int(parts[2])}"
link = pathlib.Path.cwd() / "sdk/xcode_links" / sdk.name
link.parent.mkdir(parents=True, exist_ok=True)
if not link.is_symlink():
    link.symlink_to(sdk, target_is_directory=True)
elif link.resolve() != sdk.resolve():
    sys.exit(f"Error: SDK link points to a different SDK: {link}")
source_root = pathlib.Path(__file__).resolve().parent.parent
sdk_path = "//" + str(link.relative_to(source_root))
if sys.argv[1] == "scope":
    print(f'xcode_version="{tool_version}"')
    print(f'xcode_version_int={int(tool_version)}')
    print(f'xcode_build="{build}"')
    print(f'sdk_path="{sdk_path}"')
else:
    print(sdk_path)
    print(developer / "usr/bin")
    print(build)
    print(version)
