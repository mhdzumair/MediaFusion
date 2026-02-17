import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import aiohttp
from aiohttp_socks import ProxyConnector
from PIL import Image, ImageDraw, ImageFont, ImageStat, UnidentifiedImageError

from db.config import settings
from db.redis_database import REDIS_ASYNC_CLIENT
from db.schemas import PosterData
from scrapers.imdb_data import get_imdb_rating
from utils import const

font_cache = {}
executor = ThreadPoolExecutor(max_workers=4)


async def fetch_poster_image(url: str, max_retries: int = 1) -> bytes:
    # Check if the image is cached in Redis
    cached_image = await REDIS_ASYNC_CLIENT.get(url)
    if cached_image:
        logging.info(f"Using cached image for URL: {url}")
        return cached_image

    timeout = aiohttp.ClientTimeout(
        total=10,
        connect=5,
        sock_read=5,
    )

    last_exception = None
    for attempt in range(1, max_retries + 1):
        connector = aiohttp.TCPConnector()
        if settings.requests_proxy_url:
            connector = ProxyConnector.from_url(settings.requests_proxy_url)
        try:
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                async with session.get(url, headers=const.UA_HEADER) as response:
                    response.raise_for_status()
                    content_type = response.headers.get("Content-Type", "")
                    if not content_type.lower().startswith("image/"):
                        raise ValueError(f"Unexpected content type: {content_type} for URL: {url}")
                    content = await response.read()

                    # Cache the image in Redis for 1 hour
                    logging.info(f"Caching image for URL: {url}")
                    await REDIS_ASYNC_CLIENT.set(url, content, ex=3600)
                    return content
        except ValueError:
            raise
        except (TimeoutError, aiohttp.ClientError) as e:
            last_exception = e
            if attempt < max_retries:
                logging.warning(f"Attempt {attempt}/{max_retries} failed for {url}: {e}. Retrying...")
                await asyncio.sleep(1)
            else:
                logging.warning(f"All {max_retries} attempts failed for {url}: {e}")

    raise last_exception


# Synchronous function for CPU-bound task: image processing
def process_poster_image(content: bytes, mediafusion_data: PosterData) -> BytesIO:
    try:
        image = Image.open(BytesIO(content)).convert("RGB")
        image = image.resize((300, 450))
        imdb_rating = getattr(mediafusion_data, "imdb_rating", None)

        # The add_elements_to_poster function would be synchronous
        image = add_elements_to_poster(image, imdb_rating)
        if getattr(mediafusion_data, "is_add_title_to_poster", False):
            # The add_title_to_poster function would also be synchronous
            title = getattr(mediafusion_data, "title", "")
            image = add_title_to_poster(image, title)

        image = image.convert("RGB")

        byte_io = BytesIO()
        image.save(byte_io, "JPEG")
        byte_io.seek(0)

        return byte_io
    except UnidentifiedImageError:
        poster_url = getattr(mediafusion_data, "poster", "unknown")
        raise ValueError(f"Cannot identify image from URL: {poster_url}")


async def create_poster(mediafusion_data: PosterData) -> BytesIO:
    content = await fetch_poster_image(mediafusion_data.poster)

    # Check if we need to fetch IMDb rating
    meta_id = getattr(mediafusion_data, "id", "")
    if meta_id and meta_id.startswith("tt") and mediafusion_data.imdb_rating is None:
        imdb_rating = await get_imdb_rating(meta_id)
        if imdb_rating:
            mediafusion_data.imdb_rating = imdb_rating
            # Note: Rating update is handled separately via SQL CRUD if needed

    loop = asyncio.get_event_loop()
    byte_io = await asyncio.wait_for(
        loop.run_in_executor(executor, process_poster_image, content, mediafusion_data),
        30,
    )

    return byte_io


def add_elements_to_poster(image: Image.Image, imdb_rating: float = None) -> Image.Image:
    draw = ImageDraw.Draw(image, "RGBA")
    margin = 10
    padding = 5

    # Adding IMDb rating at the bottom left with a semi-transparent background
    if imdb_rating:
        imdb_text = f" {imdb_rating}/10"
        imdb_logo = Image.open("resources/images/imdb_logo.png")
        font = load_font("resources/fonts/IBMPlexSans-Medium.ttf", 24)

        # Calculate text bounding box using the draw instance
        left, top, right, bottom = draw.textbbox((0, 0), imdb_text, font=font)
        text_width = right - left
        text_height = bottom - top

        # Resize IMDb Logo according to text height
        aspect_ratio = imdb_logo.width / imdb_logo.height
        imdb_logo = imdb_logo.resize((int(text_height * aspect_ratio), text_height))

        # Draw a semi-transparent rectangle behind the logo and rating for better visibility
        rectangle_x0 = margin
        rectangle_x1 = rectangle_x0 + imdb_logo.width + text_width + (2 * padding)
        rectangle_y0 = image.height - margin - text_height - (2 * padding)
        rectangle_y1 = image.height - margin
        draw.rounded_rectangle(
            (rectangle_x0, rectangle_y0, rectangle_x1, rectangle_y1),
            fill=(0, 0, 0, 176),
            radius=8,
        )

        # Place the IMDb Logo
        image.paste(imdb_logo, (rectangle_x0 + padding, rectangle_y0 + padding), imdb_logo)

        # Now draw the rating text
        draw.text(
            (rectangle_x0 + padding + imdb_logo.width, rectangle_y0),
            imdb_text,
            font=font,
            fill="#F5C518",
        )

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
        font_cache[(font_path, font_size)] = ImageFont.truetype(font_path, size=font_size)
    return font_cache[(font_path, font_size)]


# Function to split the title into multiple lines
def split_title(title, font, draw, max_width):
    words = title.split()
    if not words:
        return []

    # Calculate character width for common characters
    char_widths = {char: draw.textbbox((0, 0), char, font=font)[2] for char in set(title)}
    average_character_width = sum(char_widths.values()) / len(char_widths) if char_widths else 10

    lines = []
    current_line = []
    current_width = 0

    for word in words:
        # Calculate word width more accurately
        word_width = draw.textbbox((0, 0), word, font=font)[2]

        # If this is the first word on the line or the line is still empty
        if not current_line:
            current_line.append(word)
            current_width = word_width
            continue

        # Test if adding this word exceeds the max width
        space_width = char_widths.get(" ", average_character_width)
        if current_width + space_width + word_width <= max_width:
            current_line.append(word)
            current_width += space_width + word_width
        else:
            # Complete current line and start a new one
            lines.append(" ".join(current_line))
            current_line = [word]
            current_width = word_width

    # Add the last line if it's not empty
    if current_line:
        lines.append(" ".join(current_line))

    return lines


def adjust_font_and_split(title, font_path, max_width, max_lines, initial_font_size, draw, min_font_size=10):
    # Start with the largest font size and try to fit within max_lines
    # If that doesn't work, reduce font size

    for font_size in range(initial_font_size, min_font_size - 1, -1):
        font = load_font(font_path, font_size)
        lines = split_title(title, font, draw, max_width)

        # Skip empty results (shouldn't happen with improved split_title)
        if not lines:
            continue

        # If we can fit all lines within max_lines, we're done
        if len(lines) <= max_lines:
            return lines, font

    # If we get here, even the smallest font size couldn't fit within max_lines
    # So we'll use the smallest font and truncate/adjust as necessary
    font = load_font(font_path, min_font_size)
    lines = split_title(title, font, draw, max_width)

    # If we still have too many lines, truncate and add ellipsis to the last line
    if len(lines) > max_lines:
        truncated_lines = lines[: max_lines - 1]  # Leave room for last line with ellipsis

        # Handle the last line: try to add ellipsis
        last_line = lines[max_lines - 1]

        # Keep removing words until we can fit "..." at the end
        words = last_line.split()
        for i in range(len(words), 0, -1):
            partial_line = " ".join(words[:i])
            with_ellipsis = partial_line + "..."
            if draw.textbbox((0, 0), with_ellipsis, font=font)[2] <= max_width:
                truncated_lines.append(with_ellipsis)
                break

        # If even a single word with ellipsis doesn't fit, just use ellipsis
        if len(truncated_lines) < max_lines:
            truncated_lines.append("...")

        lines = truncated_lines

    # Guaranteed to return something useful
    return lines, font


def get_average_color(image, bbox):
    # Crop the image to the bounding box
    cropped_image = image.crop(bbox)
    # Get the average color of the cropped image
    stat = ImageStat.Stat(cropped_image)
    return stat.mean


def text_color_based_on_background(average_color):
    # Calculate the perceived brightness of the average color
    brightness = sum([average_color[i] * v for i, v in enumerate([0.299, 0.587, 0.114])])
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
    max_lines = 3  # Maximum number of lines for the title
    font_path = "resources/fonts/IBMPlexSans-Bold.ttf"
    initial_font_size = 50  # Starting font size which will be adjusted dynamically
    min_font_size = 20  # Minimum font size to avoid tiny text

    # Use our improved function that guarantees a result
    lines, font = adjust_font_and_split(
        title_text,
        font_path,
        max_width,
        max_lines,
        initial_font_size,
        draw,
        min_font_size,
    )

    # If no lines or font returned (shouldn't happen with improved function), use fallback
    if not lines or not font:
        # Fallback to a very simple approach
        font = load_font(font_path, min_font_size)
        lines = [title_text[:20] + "..."] if len(title_text) > 20 else [title_text]

    # Calculate the total height of the text block
    line_spacing = 10  # Additional space between lines
    text_block_height = sum(draw.textbbox((0, 0), line, font=font)[3] for line in lines) + (
        line_spacing * (len(lines) - 1)
    )

    # Ensure y is not negative or too close to the top
    y = max(0, (image.height - text_block_height) // 2)
    # Ensure sample_area is fully within image bounds
    top_y = max(0, y - text_block_height // 2)
    bottom_y = min(image.height, y + text_block_height + text_block_height // 2)
    sample_area = (0, top_y, image.width, bottom_y)

    # Ensure sample_area is valid
    if sample_area[3] <= sample_area[1]:
        sample_area = (
            0,
            0,
            image.width,
            image.height,
        )  # Default to full image if invalid

    average_color = get_average_color(image, sample_area)
    text_color, outline_color = text_color_based_on_background(average_color)

    # Draw each line of text
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_width = bbox[2] - bbox[0]
        line_height = bbox[3] - bbox[1]
        x = (image.width - line_width) // 2  # Center horizontally
        draw_text_with_outline(draw, (x, y), line, font, text_color, outline_color)
        y += line_height + line_spacing  # Move y position for next line, adding line spacing

    return image
