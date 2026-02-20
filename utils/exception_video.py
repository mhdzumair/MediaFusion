import os


def create_text_video(output_path, text, duration=30, resolution=(1280, 720), fontsize=45, bgcolor="black"):
    background_command = (
        f"ffmpeg -f lavfi -i color=c={bgcolor}:s={resolution[0]}x{resolution[1]} -t {duration} -y background.mp4"
    )
    os.system(background_command)

    lines = text.split("\n")
    line_height = fontsize + 10
    total_height = line_height * len(lines)
    start_y = f"(h-{total_height})/2"

    drawtext_filters = []
    for i, line in enumerate(lines):
        escaped = line.replace("'", "'\\''").replace(":", "\\:")
        y_expr = f"{start_y}+{i * line_height}"
        drawtext_filters.append(
            f"drawtext=text='{escaped}':fontcolor=white:fontsize={fontsize}:"
            f"x=(w-text_w)/2:y={y_expr}:shadowx=2:shadowy=2:shadowcolor=black@0.5"
        )

    vf = ",".join(drawtext_filters)
    vf += f",fade=t=in:st=0:d=1,fade=t=out:st={duration - 1}:d=1"

    text_command = f'ffmpeg -i background.mp4 -vf "{vf}" -y {output_path}'
    os.system(text_command)

    os.remove("background.mp4")


if __name__ == "__main__":
    videos = {
        "mediaflow_ip_error.mp4": "MediaFlow proxy IP lookup failed.\nPlease check your MediaFlow is working correctly.",
    }
    for name, text in videos.items():
        create_text_video(f"resources/exceptions/{name}", text)
