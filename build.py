# build.py
import os
import sys
import subprocess
import platform
import venv
from pathlib import Path

def create_venv():
    """Create a virtual environment for building."""
    venv_path = Path('venv')
    if not venv_path.exists():
        print("Creating virtual environment...")
        venv.create(venv_path, with_pip=True)
        return True
    return False

def get_venv_python():
    """Get the Python executable path from the virtual environment."""
    if platform.system() == "Windows":
        python_path = Path('venv/Scripts/python.exe')
    else:
        python_path = Path('venv/bin/python')
    return str(python_path)

def get_venv_pip():
    """Get the pip executable path from the virtual environment."""
    if platform.system() == "Windows":
        pip_path = Path('venv/Scripts/pip.exe')
    else:
        pip_path = Path('venv/bin/pip')
    return str(pip_path)

def check_dependencies():
    """Install required packages in the virtual environment."""
    # Base packages required for all platforms
    required_packages = [
        'pyinstaller',
        'requests',
        'beautifulsoup4',
        'feedparser',
        'pytz',
        'arxiv',
        'pdfplumber',
        'cloudscraper',
        'selenium',
        'webdriver-manager'
    ]
    
    # Add platform-specific packages
    if platform.system() == "Windows":
        required_packages.append('windows-curses')
    
    pip = get_venv_pip()
    
    print("Installing dependencies in virtual environment...")
    for package in required_packages:
        try:
            # All packages are now properly filtered by platform, no need for condition checking
            subprocess.check_call([pip, 'install', package])
            print(f"✓ {package}")
        except subprocess.CalledProcessError:
            print(f"✗ Failed to install {package}")
            return False
    return True

def create_spec_file():
    """Create a custom .spec file for PyInstaller."""
    spec_content = """# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

block_cipher = None
sys.modules['FixTk'] = None  # Prevent Tk/Tcl dependency issues

a = Analysis(
    ['snews.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'curses',
        'bs4',
        'feedparser',
        'pytz',
        'arxiv',
        'pdfplumber',
        'cloudscraper',
        'selenium',
        'webdriver_manager',
        'webdriver_manager.chrome',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='snews',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
"""
    with open('snews.spec', 'w') as f:
        f.write(spec_content)

def build_executable():
    """Build the executable using PyInstaller."""
    os_type = platform.system().lower()
    venv_python = get_venv_python()
    
    # Create spec file
    create_spec_file()
    
    # Get path to pyinstaller in virtual environment
    if os_type == 'windows':
        pyinstaller_path = 'venv/Scripts/pyinstaller.exe'
    else:
        pyinstaller_path = 'venv/bin/pyinstaller'
    
    # Build command - when using spec file, we don't need additional options
    cmd = [pyinstaller_path, '--clean', 'snews.spec']
    
    try:
        print(f"Building executable for {os_type}...")
        subprocess.run(cmd, check=True)
        
        # Verify the build
        dist_dir = os.path.join(os.getcwd(), 'dist')
        exe_name = 'snews.exe' if os_type == 'windows' else 'snews'
        exe_path = os.path.join(dist_dir, exe_name)
        
        if os.path.exists(exe_path):
            print(f"\nBuild successful! Executable created at: {exe_path}")
            print("\nInstallation instructions:")
            if os_type == 'windows':
                print("1. Copy snews.exe to a directory in your PATH")
                print("2. Run 'snews' from cmd or PowerShell")
            else:
                print("1. Copy snews to /usr/local/bin/: sudo cp dist/snews /usr/local/bin/")
                print("2. Make it executable: sudo chmod +x /usr/local/bin/snews")
                print("3. Run 'snews' from terminal")
        else:
            print("Build failed: Executable not found")
            return False
            
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"Build failed: {e}")
        return False

def cleanup():
    """Clean up build artifacts."""
    dirs_to_clean = ['build', '__pycache__']
    files_to_clean = ['snews.spec']
    
    print("\nCleaning up build artifacts...")
    for d in dirs_to_clean:
        if os.path.exists(d):
            try:
                import shutil
                shutil.rmtree(d)
                print(f"✓ Removed {d}/")
            except:
                print(f"✗ Failed to remove {d}/")
    
    for f in files_to_clean:
        if os.path.exists(f):
            try:
                os.remove(f)
                print(f"✓ Removed {f}")
            except:
                print(f"✗ Failed to remove {f}")

def main():
    """Main build process."""
    os_type = platform.system()
    print(f"Building snews for {os_type}")
    
    # Create virtual environment
    created_new = create_venv()
    if created_new:
        print("Virtual environment created successfully")
    else:
        print("Using existing virtual environment")
    
    if not check_dependencies():
        print("Failed to install required packages")
        return 1
    
    if not build_executable():
        print("Build failed")
        return 1
    
    cleanup()
    return 0

if __name__ == "__main__":
    sys.exit(main())
