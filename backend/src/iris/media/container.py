import av
from av.container import InputContainer


def session_origin(container: InputContainer) -> float:
    if container.start_time is None:
        return 0.0
    return container.start_time / av.time_base


def first_video_stream(container: InputContainer) -> av.VideoStream | None:
    for stream in container.streams.video:
        if not stream.disposition & av.stream.Disposition.attached_pic:
            return stream
    return None
