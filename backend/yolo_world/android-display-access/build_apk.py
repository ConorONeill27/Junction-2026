"""Download portable build tools into .tools and build the debug APK on Windows."""
import os
from pathlib import Path
import subprocess
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
TOOLS = ROOT / ".tools"


def download_zip(url, filename, destination):
    archive = TOOLS / filename
    if not archive.exists():
        print(f"Downloading {filename}...", flush=True)
        urllib.request.urlretrieve(url, archive)
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zipped:
        zipped.extractall(destination)


def main():
    TOOLS.mkdir(exist_ok=True)
    jdk_root = TOOLS / "jdk"
    if not list(jdk_root.glob("*/bin/java.exe")):
        download_zip("https://aka.ms/download-jdk/microsoft-jdk-21-windows-x64.zip", "jdk.zip", jdk_root)
    java_dir = next(jdk_root.glob("*/bin/java.exe")).parent.parent
    sdk_root = TOOLS / "sdk"
    manager = sdk_root / "cmdline-tools/latest/bin/sdkmanager.bat"
    if not manager.exists():
        download_zip("https://dl.google.com/android/repository/commandlinetools-win-15859902_latest.zip",
                     "android-tools.zip", TOOLS / "unpacked")
        manager.parent.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copytree(TOOLS / "unpacked/cmdline-tools", manager.parent.parent, dirs_exist_ok=True)
    env = {**os.environ, "JAVA_HOME": str(java_dir), "ANDROID_HOME": str(sdk_root),
           "ANDROID_USER_HOME": str(TOOLS / "android-user"), "GRADLE_USER_HOME": str(TOOLS / "gradle")}
    env["PATH"] = str(java_dir / "bin") + os.pathsep + env.get("PATH", "")
    if not (sdk_root / "platforms/android-36/android.jar").exists():
        subprocess.run([str(manager), f"--sdk_root={sdk_root}", "--licenses"], input="y\n" * 100,
                       text=True, env=env, check=True)
        subprocess.run([str(manager), f"--sdk_root={sdk_root}", "platforms;android-36", "build-tools;36.0.0", "platform-tools"],
                       input="y\n" * 100, text=True, env=env, check=True)
    local = ROOT / "local.properties"
    if not local.exists():
        local.write_text("sdk.dir=" + str(sdk_root).replace("\\", "/") + "\n", encoding="utf-8")
    subprocess.run([str(ROOT / "gradlew.bat"), "assembleDebug", "--console=plain", "--no-daemon"],
                   cwd=ROOT, env=env, check=True)
    print("APK: " + str(ROOT / "app/build/outputs/apk/debug/app-debug.apk"), flush=True)


if __name__ == "__main__":
    main()
