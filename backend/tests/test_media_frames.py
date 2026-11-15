from pathlib import Path

import av
import cv2
import numpy as np
import pytest

from iris.media.frames import (
    FrameNormaliser,
    GridSampler,
    clear_frames,
    fit,
    normalise_frame,
    output_size,
    rebuild_frames,
    to_upright_bgr,
    write_frame,
)
from iris.media.images import grey, shrink_to_width, size_within_width
from iris.models import FrameIndex, FrameRef
from iris.store.layout import (
    MAX_AUDIO_SEQ,
    MAX_FRAME_T,
    MAX_SHARE,
    SessionPaths,
    audio_chunk_name,
    captured_frame_name,
    frame_name,
    frame_path,
    read_audio_chunk_name,
    read_captured_frame_name,
    read_model,
    write_model,
)
from tests.media import COLOURS, solid_jpeg


def sampled(times: list[float], fps: float = 1.0) -> list[float]:
    sampler = GridSampler(fps)
    return [t for t in times if sampler.take(t)]


def test_the_grid_keeps_the_first_frame_of_each_cell():
    assert sampled([0.0, 0.4, 0.99, 1.0, 1.5, 2.2, 2.9]) == [0.0, 1.0, 2.2]


def test_a_frame_stamped_a_hair_early_counts_for_its_grid_point():
    assert sampled([0.0, 0.9995, 1.2]) == [0.0, 0.9995]
    assert GridSampler(1.0).cell(0.9995) == 1


def test_a_frame_earlier_than_the_rounding_tolerance_stays_in_its_cell():
    assert sampled([0.0, 0.998, 1.0]) == [0.0, 1.0]
    assert GridSampler(1.0).cell(0.998) == 0


def test_the_grid_skips_cells_with_no_frames():
    assert sampled([0.0, 3.5, 3.9, 4.0]) == [0.0, 3.5, 4.0]


def test_the_grid_follows_the_frame_rate():
    assert sampled([0.0, 0.3, 0.5, 0.7, 1.0, 1.49], fps=2.0) == [0.0, 0.5, 1.0]


@pytest.mark.parametrize(
    ("size", "rotation", "max_width", "expected"),
    [
        ((640, 360), 0, 1280, (640, 360)),
        ((1920, 1080), 0, 1280, (1280, 720)),
        ((1280, 720), 0, 1280, (1280, 720)),
        ((640, 360), 90, 1280, (360, 640)),
        ((640, 360), 270, 200, (200, 356)),
        ((640, 360), 180, 1280, (640, 360)),
        ((10_000, 1), 0, 100, (100, 1)),
    ],
    ids=[
        "narrow-kept",
        "wide-scaled-down",
        "at-the-limit",
        "quarter-turn-swaps",
        "three-quarter-turn-swaps-then-scales",
        "half-turn-keeps",
        "thin-keeps-a-pixel",
    ],
)
def test_the_output_size_is_upright_and_never_upscaled(size, rotation, max_width, expected):
    assert output_size(*size, rotation, max_width) == expected


def test_a_frame_is_turned_counterclockwise_by_its_rotation():
    image = np.zeros((4, 8, 3), np.uint8)
    image[:2] = 255
    frame = av.VideoFrame.from_ndarray(image, format="bgr24")
    upright = to_upright_bgr(frame, (4, 8), 90)
    assert upright.shape == (8, 4, 3)
    assert upright[:, :2].min() == 255 and upright[:, 2:].max() == 0


def test_fit_keeps_aspect_and_centres_on_black():
    tall = np.full((200, 50, 3), 255, np.uint8)
    out = fit(tall, (100, 100))
    assert out.shape == (100, 100, 3)
    assert out[:, :30].max() == 0 and out[:, 40:60].min() == 255


def test_fit_scales_a_small_frame_up_to_the_share_size():
    small = np.full((50, 100, 3), 255, np.uint8)
    out = fit(small, (200, 200))
    assert out.shape == (200, 200, 3)
    assert out[:40].max() == 0 and out[60:140].min() == 255 and out[160:].max() == 0


def captured(tmp_path: Path, name: str, size: tuple[int, int], colour=COLOURS[0]) -> Path:
    path = tmp_path / "live" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(solid_jpeg(colour, size))
    return path


def normaliser(tmp_path: Path, max_width: int = 1280) -> FrameNormaliser:
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir(exist_ok=True)
    return FrameNormaliser(frames_dir, max_width)


def written_size(tmp_path: Path, ref: FrameRef) -> tuple[int, int]:
    height, width = cv2.imread(str(tmp_path / ref.path)).shape[:2]
    return width, height


def test_the_first_frame_of_a_share_fixes_its_size(tmp_path: Path):
    norm = normaliser(tmp_path, max_width=320)
    first = norm.add(0.0, captured(tmp_path, "a.jpg", (640, 400)))
    later = norm.add(1.0, captured(tmp_path, "b.jpg", (800, 300)))
    assert first is not None and later is not None
    assert norm.size == norm.first_size == (320, 200)
    assert written_size(tmp_path, first) == written_size(tmp_path, later) == (320, 200)


def test_a_frame_already_at_the_share_size_is_copied_unchanged(tmp_path: Path):
    norm = normaliser(tmp_path)
    src = captured(tmp_path, "a.jpg", (320, 240))
    ref = norm.add(1.5, src)
    assert ref == FrameRef(t=1.5, path="frames/000001500.jpg")
    assert (tmp_path / ref.path).read_bytes() == src.read_bytes()


def test_a_new_share_is_sized_from_its_own_first_frame(tmp_path: Path):
    norm = normaliser(tmp_path)
    norm.add(0.0, captured(tmp_path, "a.jpg", (320, 240)))
    norm.new_share()
    ref = norm.add(5.0, captured(tmp_path, "b.jpg", (640, 360)))
    assert ref is not None
    assert norm.size == (640, 360)
    assert norm.first_size == (320, 240)
    assert written_size(tmp_path, ref) == (640, 360)


def test_an_unreadable_frame_is_skipped_and_does_not_fix_the_size(tmp_path: Path):
    norm = normaliser(tmp_path)
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"\xff\xd8\xffnot a jpeg")
    assert norm.add(0.0, broken) is None
    assert norm.size is None
    assert list((tmp_path / "frames").iterdir()) == []
    norm.add(1.0, captured(tmp_path, "a.jpg", (320, 240)))
    assert norm.size == (320, 240)


def test_a_frame_normalised_to_a_given_size_is_fitted_copied_or_skipped(tmp_path: Path):
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"\xff\xd8\xffnot a jpeg")
    same = captured(tmp_path, "a.jpg", (320, 240))
    fitted = normalise_frame(frames_dir, 1.0, captured(tmp_path, "b.jpg", (640, 360)), (320, 240))
    copied = normalise_frame(frames_dir, 2.0, same, (320, 240))
    assert fitted is not None and copied is not None
    assert written_size(tmp_path, fitted) == (320, 240)
    assert (tmp_path / copied.path).read_bytes() == same.read_bytes()
    assert normalise_frame(frames_dir, 3.0, broken, (320, 240)) is None
    assert sorted(p.name for p in frames_dir.iterdir()) == ["000001000.jpg", "000002000.jpg"]


def test_a_written_frame_is_named_by_its_time(tmp_path: Path):
    image = np.full((24, 32, 3), 128, np.uint8)
    ref = write_frame(tmp_path, 2.0004, image)
    assert ref == FrameRef(t=2.0004, path="frames/000002000.jpg")
    assert (tmp_path / "000002000.jpg").exists()


def test_a_frame_that_cannot_be_written_fails(tmp_path: Path):
    image = np.full((24, 32, 3), 128, np.uint8)
    with pytest.raises(OSError, match="could not write frame 000001000.jpg"):
        write_frame(tmp_path / "missing", 1.0, image)


def test_a_sampled_frame_is_named_by_its_time_in_milliseconds():
    assert frame_name(1.0) == "000001000.jpg"
    assert frame_name(0.9996) == "000001000.jpg"
    assert frame_path(12.3456) == "frames/000012346.jpg"


@pytest.mark.parametrize(
    ("t", "share"),
    [(0.0, 1), (1.5, 3), (MAX_FRAME_T, MAX_SHARE)],
    ids=["first", "later-share", "largest"],
)
def test_a_captured_frame_name_reads_back_as_its_time_and_share(t, share):
    name = captured_frame_name(t, share)
    assert read_captured_frame_name(name) == (t, share)


def test_a_captured_frame_name_is_fixed_width():
    assert captured_frame_name(1.5, 3) == "000001500-0003.jpg"


def test_a_frame_captured_before_share_numbers_belongs_to_the_first_share():
    assert read_captured_frame_name("000001500.jpg") == (1.5, 1)


@pytest.mark.parametrize(
    "name",
    ["frame.jpg", "1500.jpg", "000001500-03.jpg", "000001500-0003.jpg.tmp", "000001500.png"],
)
def test_other_files_are_not_captured_frames(name):
    assert read_captured_frame_name(name) is None


@pytest.mark.parametrize("seq", [0, 7, MAX_AUDIO_SEQ], ids=["first", "later", "largest"])
def test_an_audio_chunk_name_reads_back_as_its_number(seq):
    assert read_audio_chunk_name(audio_chunk_name(seq)) == seq


def test_an_audio_chunk_name_is_fixed_width():
    assert audio_chunk_name(7) == "000007.webm"


@pytest.mark.parametrize("name", ["7.webm", "000007.webm.tmp", "000007.ogg", "header.webm"])
def test_other_files_are_not_audio_chunks(name):
    assert read_audio_chunk_name(name) is None


def test_captured_files_live_in_the_live_folders(paths: SessionPaths):
    assert paths.live_audio_chunk(3) == paths.live_audio_dir / "000003.webm"
    assert paths.live_frame(2.0, 1) == paths.live_frames_dir / "000002000-0001.jpg"


def test_clearing_frames_removes_only_sampled_frames(paths: SessionPaths):
    (paths.frames_dir / "000001000.jpg").write_bytes(b"old")
    (paths.frames_dir / "keep.txt").write_bytes(b"x")
    clear_frames(paths)
    assert [p.name for p in paths.frames_dir.iterdir()] == ["keep.txt"]


def test_clearing_frames_creates_the_folder(tmp_path: Path):
    paths = SessionPaths(tmp_path / "s1")
    clear_frames(paths)
    assert paths.frames_dir.is_dir()


def test_the_frame_index_is_removed_first_and_written_last(paths: SessionPaths):
    old = FrameIndex(fps=1.0, width=1, height=1, frames=[])
    write_model(paths.frame_index, old)
    (paths.frames_dir / "000009000.jpg").write_bytes(b"stale")
    new = FrameIndex(fps=1.0, width=2, height=2, frames=[FrameRef(t=0.0, path="frames/a.jpg")])
    seen_while_writing = []

    def write_frames() -> FrameIndex:
        seen_while_writing.append(
            (paths.frame_index.exists(), sorted(p.name for p in paths.frames_dir.iterdir()))
        )
        return new

    assert rebuild_frames(paths, write_frames) == new
    assert seen_while_writing == [(False, [])]
    assert read_model(paths.frame_index, FrameIndex) == new


def test_frames_that_fail_to_write_leave_no_frame_index(paths: SessionPaths):
    write_model(paths.frame_index, FrameIndex(fps=1.0, width=1, height=1, frames=[]))

    def write_frames() -> FrameIndex:
        raise OSError("disk full")

    with pytest.raises(OSError, match="disk full"):
        rebuild_frames(paths, write_frames)
    assert not paths.frame_index.exists()


def test_a_colour_image_turns_grey_and_a_grey_one_is_kept():
    colour = np.full((4, 6, 3), (0, 0, 255), np.uint8)
    assert grey(colour).shape == (4, 6)
    already = np.full((4, 6), 9, np.uint8)
    assert grey(already) is already


@pytest.mark.parametrize(
    ("size", "max_width", "expected"),
    [((640, 360), 1280, (640, 360)), ((1280, 720), 640, (640, 360)), ((5000, 1), 50, (50, 1))],
    ids=["narrow-kept", "wide-scaled-down", "thin-keeps-a-pixel"],
)
def test_a_size_is_brought_within_a_width_keeping_aspect(size, max_width, expected):
    assert size_within_width(*size, max_width) == expected


def test_a_wide_image_shrinks_to_the_width_and_a_narrow_one_is_kept():
    wide = np.zeros((360, 1280, 3), np.uint8)
    assert shrink_to_width(wide, 640).shape == (180, 640, 3)
    narrow = np.zeros((10, 20, 3), np.uint8)
    assert shrink_to_width(narrow, 640) is narrow
