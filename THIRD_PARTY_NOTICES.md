# Third-party software

Vox Lupine's own code is licensed under the GNU General Public License v3.0 (see
`LICENSE`).

- **Source version:** the repository contains no copies of the libraries below;
  `setup.bat` downloads them from PyPI into a local `.venv`.
- **Installer (`VoxLupine-Setup-*.exe`):** bundles the libraries below plus the Python
  runtime, built with PyInstaller. Each component stays under its own license. The
  corresponding source code for every bundled component is available from the links
  below (pedalboard at the version pinned in `requirements.txt`), and Vox Lupine's
  own source is in this repository.

| Component | Used for | License |
|---|---|---|
| [pedalboard](https://github.com/spotify/pedalboard) (Spotify) | Audio filters and effects | GPL-3.0 |
| [NumPy](https://github.com/numpy/numpy) (bundles OpenBLAS) | Audio math | BSD-3-Clause and others (see its package) |
| [python-soundfile](https://github.com/bastibe/python-soundfile) (bundles [libsndfile](https://github.com/libsndfile/libsndfile)) | Reading/writing WAV files | BSD-3-Clause; libsndfile: LGPL-2.1 (shipped as a separate, replaceable DLL) |
| [python-sounddevice](https://github.com/spatialaudio/python-sounddevice) (bundles [PortAudio](https://github.com/PortAudio/portaudio)) | Live audio input/output | MIT; PortAudio: MIT |
| [CFFI](https://github.com/python-cffi/cffi) | Used by sounddevice/soundfile | MIT |
| [Python](https://www.python.org/) with Tkinter / Tcl/Tk | Runtime and the app window | PSF License; Tcl/Tk license (BSD-style) |
| [PyInstaller](https://github.com/pyinstaller/pyinstaller) bootloader (installer only) | Starts the bundled app | GPL-2.0 with an exception that allows distributing bundled apps |
| [Inno Setup](https://jrsoftware.org/isinfo.php) (installer only) | The setup wizard | Inno Setup License (free, including for distributing installers) |

Because pedalboard is GPL-3.0, Vox Lupine is distributed under GPL-3.0 as well.

## Not included

**VB-Cable** (VB-Audio Virtual Cable) is a separate product by VB-Audio Software,
distributed as donationware from <https://vb-audio.com/Cable/>. Vox Lupine only sends
audio to it; it is not bundled, and this project is not affiliated with VB-Audio.

## References

The built-in EQ presets use frequency ranges described in the Audio Issues EQ
cheat sheet: <https://www.audio-issues.com/music-mixing/all-the-eq-information-youll-ever-need/>.
No content from that article is reproduced here.
