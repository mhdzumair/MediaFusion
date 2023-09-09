import os


def create_text_video(output_path, text, duration=30, resolution=(1280, 720), fontsize=45, bgcolor="black"):
    # Create a solid color background using FFmpeg
    background_command = (
        f"ffmpeg -f lavfi -i color=c={bgcolor}:s={resolution[0]}x{resolution[1]} -t {duration} -y background.mp4"
    )
    os.system(background_command)

    # Overlay the text with shadow and fade-in and fade-out effects
    text_command = (
        f'ffmpeg -i background.mp4 -vf "drawbox=y=ih/4-10:color=black@0.4:width=iw:height=128:t=2,'
        f"drawtext=text='{text}':fontcolor=white:fontsize={fontsize}:"
        f"x=(w-text_w)/2:y=(h-text_h)/2:shadowx=2:shadowy=2:shadowcolor=black@0.5,"
        f'fade=t=in:st=0:d=1,fade=t=out:st={duration-1}:d=1" -y {output_path}'
    )
    os.system(text_command)

    # Remove the temporary solid color background video
    os.remove("background.mp4")


if __name__ == "__main__":
    create_text_video(
        "not_enough_space.mp4",
        "Not enough space in Seedr account to add this torrent.\n Please remove some files and try again.",
    )
    create_text_video(
        "transfer_error.mp4",
        "Error transferring magnet link to Seedr",
    )
    create_text_video(
        "torrent_not_downloaded.mp4",
        "Torrent not downloaded yet. Please try again shortly.",
    )
