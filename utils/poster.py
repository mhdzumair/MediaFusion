import asyncio
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import aiohttp
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError, ImageStat
from imdb import Cinemagoer

from db.models import MediaFusionMetaData
from utils import const

ia = Cinemagoer()
font_cache = {}
executor = ThreadPoolExecutor(max_workers=4)


async def fetch_poster_image(url: str) -> bytes:
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=10, headers=const.UA_HEADER) as response:
            response.raise_for_status()
            if not response.headers["Content-Type"].lower().startswith("image/"):
                raise ValueError(
                    f"Unexpected content type: {response.headers['Content-Type']} for URL: {url}"
                )
            return await response.read()


# Synchronous function for CPU-bound task: image processing
def process_poster_image(
    content: bytes, mediafusion_data: MediaFusionMetaData
) -> BytesIO:
    try:
        image = Image.open(BytesIO(content)).convert("RGBA")
        image = image.resize((300, 450))
        imdb_rating = None  # Assume you fetch this rating elsewhere if needed

        # The add_elements_to_poster function would be synchronous
        image = add_elements_to_poster(image, imdb_rating)
        if mediafusion_data.is_add_title_to_poster:
            # The add_title_to_poster function would also be synchronous
            image = add_title_to_poster(image, mediafusion_data.title)

        image = image.convert("RGB")

        byte_io = BytesIO()
        image.save(byte_io, "JPEG")
        byte_io.seek(0)

        return byte_io
    except UnidentifiedImageError:
        raise ValueError(f"Cannot identify image from URL: {mediafusion_data.poster}")


async def create_poster(mediafusion_data: MediaFusionMetaData) -> BytesIO:
    content = await fetch_poster_image(mediafusion_data.poster)

    loop = asyncio.get_event_loop()
    byte_io = await asyncio.wait_for(
        loop.run_in_executor(executor, process_poster_image, content, mediafusion_data),
        30,
    )

    return byte_io


def add_elements_to_poster(
    image: Image.Image, imdb_rating: float = None
) -> Image.Image:
    draw = ImageDraw.Draw(image)
    margin = 10

    # Adding IMDb rating at the bottom left with a semi-transparent background
    if imdb_rating:
        font = load_font("resources/fonts/IBMPlexSans-Medium.ttf", 24)
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


def load_font(font_path, font_size):
    if (font_path, font_size) not in font_cache:
        font_cache[(font_path, font_size)] = ImageFont.truetype(
            font_path, size=font_size
        )
    return font_cache[(font_path, font_size)]


# Function to split the title into multiple lines
def split_title(title, font, draw, max_width):
    lines = []
    words = title.split()
    average_character_width = sum(
        draw.textbbox((0, 0), char, font=font)[2] for char in set(title)
    ) / len(set(title))
    while words:
        line = ""
        line_width = 0
        while words and line_width + average_character_width <= max_width:
            word_width = draw.textbbox((0, 0), words[0], font=font)[2]
            if line_width + word_width <= max_width:
                line += words.pop(0) + " "
                line_width += word_width + average_character_width  # Add space width
            else:
                break
        lines.append(line.strip())
    return lines


# Function to adjust font size and split title
def adjust_font_and_split(title, font_path, max_width, initial_font_size, draw):
    font_size = initial_font_size
    font = load_font(font_path, font_size)
    lines = split_title(title, font, draw, max_width)
    while True:
        # Get the bounding box of the last line of text
        bbox = draw.textbbox((0, 0), lines[-1], font=font)
        text_width = bbox[2] - bbox[0]
        if len(lines) > 2 or text_width >= max_width:
            font_size -= 1  # Decrease font size by 1
            font = load_font(font_path, font_size)
            lines = split_title(title, font, draw, max_width)
        else:
            break
    return lines, font


def get_average_color(image, bbox):
    # Crop the image to the bounding box
    cropped_image = image.crop(bbox)
    # Get the average color of the cropped image
    stat = ImageStat.Stat(cropped_image)
    return stat.mean


def text_color_based_on_background(average_color):
    # Calculate the perceived brightness of the average color
    brightness = sum(
        [average_color[i] * v for i, v in enumerate([0.299, 0.587, 0.114])]
    )
    if brightness > 128:
        return "black", "white"  # Dark text, light outline
    else:
        return "white", "black"  # Light text, dark outline


# Function to draw text with an outline
def draw_text_with_outline(draw, position, text, font, fill_color, outline_color):
    x, y = position
    # Slightly thicker outlines can be more performant than multiple thin ones
    outline_width = 3
    # Offset coordinates for the outline
    outline_offsets = [
        (dx, dy)
        for dx in range(-outline_width, outline_width + 1)
        for dy in range(-outline_width, outline_width + 1)
        if dx or dy
    ]
    for offset in outline_offsets:
        draw.text((x + offset[0], y + offset[1]), text, font=font, fill=outline_color)
    draw.text(position, text, font=font, fill=fill_color)


def add_title_to_poster(image: Image.Image, title_text: str) -> Image.Image:
    draw = ImageDraw.Draw(image)
    max_width = image.width - 20  # max width for the text
    font_path = "resources/fonts/IBMPlexSans-Bold.ttf"
    initial_font_size = 50  # Starting font size which will be adjusted dynamically

    lines, font = adjust_font_and_split(
        title_text, font_path, max_width, initial_font_size, draw
    )

    # Calculate the total height of the text block using textbbox
    text_block_height = sum(draw.textbbox((0, 0), line, font=font)[3] for line in lines)

    # Starting y position, centered vertically
    y = (image.height - text_block_height) // 2
    sample_area = (0, y, image.width, y + text_block_height)
    average_color = get_average_color(image, sample_area)
    text_color, outline_color = text_color_based_on_background(average_color)

    # Draw each line of text
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_width = bbox[2] - bbox[0]
        line_height = bbox[3] - bbox[1]
        x = (image.width - line_width) // 2  # Center horizontally
        draw_text_with_outline(draw, (x, y), line, font, text_color, outline_color)
        y += line_height  # Move y position for next line

    return image
