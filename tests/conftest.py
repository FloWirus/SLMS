"""Shared fixtures.

The audio fixtures are plain PCM .wav files written with the standard
library, not files produced by ffmpeg: the test suite has to run anywhere
(CI, a fresh checkout, a machine without ffmpeg installed), and .wav happens
to also exercise the most fragile tag path in the app -- see
tags._easy_wave.
"""

import os
import struct
import wave
from pathlib import Path

import pytest

# Qt is imported by some of the modules under test; without this they need a
# display server that a test runner doesn't have.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def write_wav(path: Path, frequency: int = 440, seconds: float = 0.1) -> Path:
    """A tiny mono 8kHz PCM file. Distinct frequencies give distinct file
    contents, which is what the hash-based "is this the same track" logic
    keys on."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rate = 8000
    frames = bytearray()
    for i in range(int(rate * seconds)):
        value = int(10000 * ((i * frequency // rate) % 2 * 2 - 1))
        frames += struct.pack("<h", value)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))
    return path


@pytest.fixture
def wav_factory(tmp_path):
    def make(relative: str, frequency: int = 440) -> Path:
        return write_wav(tmp_path / relative, frequency=frequency)

    return make


@pytest.fixture
def library(tmp_path):
    """A two-track album on disk, ready to scan."""
    root = tmp_path / "library"
    write_wav(root / "Artist" / "Album" / "01.wav", frequency=440)
    write_wav(root / "Artist" / "Album" / "02.wav", frequency=660)
    return root
