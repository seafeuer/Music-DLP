# -*- mode: python ; coding: utf-8 -*-
"""
Music DLP - PyInstaller Spec File

Build with:  pyinstaller MusicDLP.spec --noconfirm
Or use:      python build_exe.py
"""

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

block_cipher = None

# Collect packages with non-Python assets or dynamic imports
ctk_datas, ctk_binaries, ctk_hiddenimports = collect_all('customtkinter')
dd_datas, dd_binaries, dd_hiddenimports = collect_all('darkdetect')
ytdlp_datas, ytdlp_binaries, ytdlp_hiddenimports = collect_all('yt_dlp')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=ctk_binaries + dd_binaries + ytdlp_binaries,
    datas=ctk_datas + dd_datas + ytdlp_datas,
    hiddenimports=(
        ctk_hiddenimports + dd_hiddenimports + ytdlp_hiddenimports + [
            # PIL / Pillow
            'PIL._tkinter_finder',
            'PIL.Image',
            'PIL.ImageTk',
            # Mutagen submodules
            'mutagen',
            'mutagen.mp3',
            'mutagen.mp4',
            'mutagen.id3',
            'mutagen.id3._frames',
            # Other dependencies
            'requests',
            'colorama',
            'ffmpeg',
            'json',
            'sqlite3',
            'configparser',
            # Application modules
            'core',
            'core.database',
            'core.config_manager',
            'core.logger',
            'converters',
            'converters.ffmpeg_converter',
            'converters.metadata_embedder',
            'downloaders',
            'downloaders.yt_dlp_manager',
            'downloaders.metadata_extractor',
            'downloaders.thumbnail_processor',
            'downloaders.url_utils',
            'gui',
            'gui.main_window',
            'gui.components.playlist_panel',
            'gui.components.song_panel',
            'gui.components.pipeline_panel',
            'gui.components.log_panel',
            'gui.dialogs.settings_dialog',
            'gui.workers.download_worker',
            'gui.workers.conversion_worker',
        ]
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'pytest', 'unittest', '_pytest',
        'test_phase2', 'test_phase3', 'test_phase4', 'test_phase5',
        'test_prompts_v2', 'test_vtuber_prompts', 'test_vtuber_prompts_v3',
        'test_database_methods', 'run_all_tests',
        'check_database_state', 'check_playlist_videos',
        'cleanup_orphan_files', 'cleanup_orphaned_videos',
        'diagnose_orphaned_files', 'fix_database_integrity',
        'find_missing_methods',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # --onedir mode
    name='MusicDLP',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Set console=True for initial testing, change to False for release
    console=True,
    disable_windowed_traceback=False,
    # icon='assets/icon.ico',  # Uncomment when icon is available
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='MusicDLP',
)
