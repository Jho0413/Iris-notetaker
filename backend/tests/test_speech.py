"""Speech stretches: Silero's rule for what is speech, faster-whisper's for what is cut out.

The rules are tested on made-up per-slice probabilities. The real model is run on silence, noise
and clicks (none of which may be sent); it hears no voice in synthetic sound, so "speech is kept"
is covered by the stage and lane tests, through the conftest stand-in.
"""

import numpy as np
import pytest

from iris import speech
from iris.speech import (
    MIN_GAP_S,
    PAD_S,
    SLICE_S,
    speech_runs,
    speech_stretches,
)

SILERO = speech.voice_probabilities  # the real model, saved before conftest's stand-in
RATE = 16_000


def probs(*pieces: tuple[float, float]) -> np.ndarray:
    """Per-slice probabilities from (probability, seconds) pieces."""
    return np.concatenate([np.full(round(s / SLICE_S), p, np.float32) for p, s in pieces])


def at(i: int) -> float:
    return i * SLICE_S


# --- What is speech (Silero's rule) --------------------------------------------------------------


def test_no_slices_means_no_speech():
    assert speech_runs(np.zeros(0, np.float32)) == []


def test_speech_starts_at_half_and_ends_after_100_ms_under_035():
    p = probs((0.0, at(10)), (0.9, at(20)), (0.1, at(10)))
    assert speech_runs(p) == [pytest.approx((at(10), at(30)))]


def test_a_short_dip_inside_speech_stays_inside_one_run():
    dip = at(3)  # 96 ms under 0.35: shorter than the 100 ms that ends speech
    p = probs((0.9, at(20)), (0.0, dip), (0.9, at(20)), (0.0, at(10)))
    assert speech_runs(p) == [pytest.approx((0.0, at(43)))]


def test_a_score_between_035_and_05_neither_starts_nor_ends_speech():
    assert speech_runs(probs((0.4, at(40)))) == []
    p = probs((0.9, at(10)), (0.4, at(20)), (0.1, at(10)))
    assert speech_runs(p) == [pytest.approx((0.0, at(30)))]


@pytest.mark.parametrize(("slices", "kept"), [(7, False), (8, True)])
def test_a_run_of_250_ms_or_less_is_dropped(slices: int, kept: bool):
    # 7 slices are 224 ms, 8 are 256 ms.
    p = probs((0.0, at(10)), (0.9, at(slices)), (0.0, at(10)))
    assert bool(speech_runs(p)) is kept


def test_speech_still_going_at_the_end_runs_to_the_end():
    p = probs((0.0, at(10)), (0.9, at(20)))
    assert speech_runs(p) == [pytest.approx((at(10), at(30)))]


def test_a_click_scoring_high_for_a_slice_or_two_is_not_speech():
    p = probs(*[piece for _ in range(10) for piece in ((0.9, at(2)), (0.0, at(15)))])
    assert speech_runs(p) == []


# --- What is cut out (faster-whisper's gap and padding) ------------------------------------------


def test_speech_less_than_2_s_apart_is_one_stretch():
    p = probs((0.9, 1.0), (0.0, MIN_GAP_S - 0.2), (0.9, 1.0), (0.0, 3.0))
    n = round(len(p) * SLICE_S * RATE)
    [(a, b)] = speech_stretches(p, n, RATE)
    assert a == 0  # padding stops at the piece's start
    assert b / RATE == pytest.approx(1.0 + 1.8 + 1.0 + PAD_S, abs=SLICE_S)


def test_a_gap_of_2_s_or_more_is_cut_out_and_each_side_padded():
    p = probs((0.0, at(30)), (0.9, at(30)), (0.0, at(150)), (0.9, at(30)), (0.0, at(30)))
    stretches = [(a / RATE, b / RATE) for a, b in speech_stretches(p, round(at(270) * RATE), RATE)]
    assert stretches == [
        pytest.approx((at(30) - PAD_S, at(60) + PAD_S)),
        pytest.approx((at(210) - PAD_S, at(240) + PAD_S)),
    ]


def test_padding_stays_within_the_piece():
    p = probs((0.9, 1.0))
    assert speech_stretches(p, RATE, RATE) == [(0, RATE)]


def test_stretches_are_in_samples_of_the_pieces_own_rate():
    p = probs((0.0, at(30)), (0.9, at(30)), (0.0, at(30)))
    assert speech_stretches(p, round(at(90) * 8_000), 8_000) == [
        (round((at(30) - PAD_S) * 8_000), round((at(60) + PAD_S) * 8_000))
    ]


@pytest.mark.parametrize("seed", range(40))
def test_stretches_are_ordered_apart_and_within_the_piece(seed: int):
    rng = np.random.default_rng(seed)
    p = np.repeat(rng.random(rng.integers(1, 60)), rng.integers(1, 40)).astype(np.float32)
    n = round(len(p) * SLICE_S * RATE) + int(rng.integers(0, 600))
    stretches = speech_stretches(p, n, RATE)
    assert all(0 <= a < b <= n for a, b in stretches)
    assert all(b1 < a2 for (_, b1), (a2, _) in zip(stretches, stretches[1:], strict=False))


# --- The real model: nothing without speech is sent ----------------------------------------------


def no_speech(samples: np.ndarray, rate: int = RATE) -> bool:
    return speech_stretches(SILERO(samples, rate), len(samples), rate) == []


def test_silence_is_not_speech():
    assert no_speech(np.zeros(5 * RATE, np.int16))


@pytest.mark.parametrize("dbfs", [-60, -40])
def test_room_noise_is_not_speech(dbfs: int):
    rng = np.random.default_rng(0)
    noise = rng.standard_normal(5 * RATE) * 32768 * 10 ** (dbfs / 20)
    assert no_speech(noise.astype(np.int16))


def test_fan_noise_is_not_speech():
    rng = np.random.default_rng(0)
    x = np.cumsum(rng.standard_normal(5 * RATE))  # brown noise: a low rumble
    x -= np.convolve(x, np.ones(1600) / 1600, mode="same")
    assert no_speech((x / x.std() * 32768 * 10 ** (-35 / 20)).astype(np.int16))


def test_clicks_are_not_speech():
    rng = np.random.default_rng(0)
    x = rng.standard_normal(6 * RATE) * 30
    click = 20_000 * rng.standard_normal(80) * np.exp(-np.arange(80) / 15)  # a 5 ms knock
    for start in range(RATE // 4, len(x) - 80, int(0.6 * RATE)):
        x[start : start + 80] += click
    assert no_speech(np.clip(x, -32768, 32767).astype(np.int16))


def test_audio_shorter_than_a_slice_is_not_scored():
    assert len(SILERO(np.zeros(speech.SLICE - 1, np.int16), RATE)) == 0
    assert speech.find_speech(np.zeros(10, np.int16), RATE) == []


def test_8_khz_audio_is_scored_as_16_khz_slices():
    samples = np.zeros(8_000 * 3, np.int16)
    assert len(SILERO(samples, 8_000)) == int(3 / SLICE_S)
    assert no_speech(samples, 8_000)
