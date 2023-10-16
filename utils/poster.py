from io import BytesIO

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError
from imdb import Cinemagoer
import requests

from db.models import MediaFusionMetaData

ia = Cinemagoer()


async def create_poster(mediafusion_data: MediaFusionMetaData) -> BytesIO:
    response = requests.get(mediafusion_data.poster, timeout=10)
    response.raise_for_status()

    # Check if the response content type is an image
    if not response.headers["Content-Type"].startswith("image/"):
        raise ValueError(
            f"Unexpected content type: {response.headers['Content-Type']} for URL: {mediafusion_data.poster}"
        )

    # Check if the response content is not empty
    if not response.content:
        raise ValueError(f"Empty content for URL: {mediafusion_data.poster}")

    try:
        image = Image.open(BytesIO(response.content))
    except UnidentifiedImageError:
        raise ValueError(f"Cannot identify image from URL: {mediafusion_data.poster}")

    image = image.resize((300, 450))
    imdb_rating = None
    if mediafusion_data.id.startswith("tt"):
        result = ia.get_movie(mediafusion_data.id[2:], info="main")
        imdb_rating = result.get("rating")

    image = add_elements_to_poster(image, imdb_rating)
    image = image.convert("RGB")

    byte_io = BytesIO()
    image.save(byte_io, "JPEG")
    byte_io.seek(0)

    return byte_io


def add_elements_to_poster(
    image: Image.Image, imdb_rating: float = None
) -> Image.Image:
    draw = ImageDraw.Draw(image)
    margin = 10

    # Adding IMDb rating at the bottom left with a semi-transparent background
    if imdb_rating:
        font = ImageFont.truetype("resources/fonts/IBMPlexSans-Medium.ttf", size=24)
        imdb_text = f"IMDb: {imdb_rating}/10"

        # Calculate text bounding box using the draw instance
        left, top, right, bottom = draw.textbbox((0, 0), imdb_text, font=font)
        text_width = right - left
        text_height = bottom - top

        # Draw a semi-transparent rectangle behind the text for better visibility
        rectangle_x0 = margin
        rectangle_y0 = image.height - text_height - margin - 5  # 5 for a little padding
        rectangle_x1 = rectangle_x0 + text_width + 10  # 10 for padding
        rectangle_y1 = image.height - margin

        draw.rectangle(
            (rectangle_x0, rectangle_y0, rectangle_x1, rectangle_y1),
            fill=(0, 0, 0, 128),
        )
        draw.text(
            (rectangle_x0 + 5, rectangle_y0), imdb_text, font=font, fill="#F5C518"
        )  # 5 for padding

    # Add MediaFusion watermark at the top right
    watermark = Image.open("resources/images/logo_text.png")

    # Resizing the watermark to fit the new poster size
    aspect_ratio = watermark.width / watermark.height
    new_width = int(image.width * 0.5)  # Reduced size for better aesthetics
    new_height = int(new_width / aspect_ratio)
    watermark = watermark.resize((new_width, new_height))

    # Position watermark at top right
    watermark_position = (image.width - watermark.width - margin, margin)
    image.paste(watermark, watermark_position, watermark)

    return image
