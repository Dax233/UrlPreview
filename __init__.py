from nonebot import get_plugin_config, on_message
from nonebot.plugin import PluginMetadata
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment, GroupMessageEvent
import requests
from bs4 import BeautifulSoup
from PIL import Image
from io import BytesIO
import base64
import os
import re
import subprocess
from urllib.parse import urlparse, urlunparse
from ..logger import model
from .config import Config
from twikit import Client
import httpx
from PIL import Image, ImageDraw, ImageFont
import asyncio
import math

__plugin_meta__ = PluginMetadata(
    name="WebsiteShortcut",
    description="自动检测群内发送的链接并生成网站的信息快照，包括网站名称、内容摘要和图片。",
    usage="发送包含链接的消息，机器人会自动回复该链接的快照信息。",
    config=Config,
)

config = get_plugin_config(Config)

# 缓存200条URL预览数据
MAX_CACHE_SIZE = 200
preview_cache = {}

# 配置 Twikit 客户端并设置代理
proxy = "http://127.0.0.1:7890"
client = Client('en-US', proxy=proxy)
proxies = {
  'http': 'http://127.0.0.1:7890',
  'https': 'http://127.0.0.1:7890',
}

def add_watermark(image_path, watermark_text="刻上属于你的痕迹", font_path="src/fonts/lolita.ttf"):
    base = Image.open(image_path).convert('RGBA')
    width, height = base.size

    # Create a transparent overlay
    txt = Image.new('RGBA', base.size, (255, 255, 255, 0))

    # Load the font
    font_size = 36  # You can adjust the font size as needed
    font = ImageFont.truetype(font_path, font_size)
    draw = ImageDraw.Draw(txt)

    # Position the text at the bottom right
    text_width, text_height = draw.textsize(watermark_text, font)
    position = (width - text_width - 10, height - text_height - 10)

    # Apply the text to the overlay
    draw.text(position, watermark_text, fill=(255, 255, 255, 128), font=font)

    # Combine the base image with the overlay
    watermarked = Image.alpha_composite(base, txt)

    # Save the result
    parts = image_path.split('.')
    if parts[1] == 'jpeg' or parts[1] == 'jpg':
        new_path = parts[0] + '.png'
        watermarked.save(new_path)
        os.remove(image_path)
    else:
        watermarked.save(image_path)

# 监听群消息
link_detector = on_message(rule=lambda event: isinstance(event, GroupMessageEvent))
bili_keyword = ["www.bilibili.com/video/", "b23.tv"]
x_keyword = ['x.com', 'twitter.com']

EHENTAI_RE = re.compile(r'https://e-hentai\.org/g/\d+/[\w-]+')
EXHENTAI_RE = re.compile(r'https://exhentai\.org/g/\d+/[\w-]+')
NHENTAI_RE = re.compile(r'https://nhentai\.(net|to)/g/\d+')
PIXIV_RE = re.compile(r'https://www\.pixiv\.net/artworks/\d+')
e_keyword = [EHENTAI_RE, EXHENTAI_RE, NHENTAI_RE, PIXIV_RE]


@link_detector.handle()
async def handle_group_message(bot: Bot, event: Event):
    message = str(event.get_message())
    biliurl = False
    xurl = False
    eurl = False
    dlurl = False
    if ('http' in message and '[CQ:json' in message) or ('http' in message and '[CQ:' not in message):
        url = extract_url(message)
        for i in bili_keyword:
            if i in url:
                biliurl = True
                break
        for i in x_keyword:
            if i in url:
                xurl = True
                break
        for i in e_keyword:
            if i.match(url):
                eurl = True
                break

        if 'asmr.one' in url:
            dlurl = True
        if 'dlsite.com' in url:
            dlurl = True

        if biliurl:
            snapshot, image_path = await generate_bilibili_snapshot(url)

        # 单独处理x链接
        elif xurl:
            url = re.sub(r'twitter.com', 'x.com', url)
            snapshot, media_paths = await generate_x_snapshot(url)
            if media_paths:
                respone = ''
                for media_path in media_paths:
                    if media_path.endswith('.jpg') or media_path.endswith('.png'):
                        add_watermark(media_path)
                        parts = media_path.split('.')
                        if parts[1] == 'jpeg' or parts[1] == 'jpg':
                            new_path = parts[0] + '.png'
                        respone = respone + MessageSegment.image(f'file:///{new_path}') + '\n'
                        # await bot.send(event, MessageSegment.image(f'file:///{media_path}'))
                    elif media_path.endswith('.mp4'):
                        try:
                            await bot.send(event, MessageSegment.video(f'file:///{media_path}'))
                        except:
                            await bot.send(event, MessageSegment.reply(event.message_id) + "视频发送失败！")
                await bot.send(event, MessageSegment.reply(event.message_id) + MessageSegment.text(snapshot) + respone)
            else:
                await bot.send(event, MessageSegment.reply(event.message_id) + snapshot)
            return

        elif eurl:
            return
        elif dlurl:
            snapshot, image_path = await generate_dlsite_snapshot(url)
            if snapshot == None:
                snapshot, image_path = await generate_snapshot(url)
                if snapshot == None:
                    return
        else:
            snapshot, image_path = await generate_snapshot(url)
            if snapshot == None:
                return
        if image_path:
            await bot.send(event, MessageSegment.reply(event.message_id) + MessageSegment.text(snapshot) + MessageSegment.image(f'file:///{image_path}'))
            model.save_message('private' if event.is_tome() else 'group', getattr(event, 'group_id', None), event.get_user_id(), 'bot发出->触发者:', "[CQ:plugin,链接预览]")
        else:
            await bot.send(event, MessageSegment.reply(event.message_id) + snapshot)
            model.save_message('private' if event.is_tome() else 'group', getattr(event, 'group_id', None), event.get_user_id(), 'bot发出->触发者:', "[CQ:plugin,链接预览]")


def extract_links(text):
    # 正则表达式匹配URL
    pattern = r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
    # 查找所有匹配的URL
    links = re.findall(pattern, text)
    return links[0]

def generate_star_rating(rating):
    if rating == 'N/A':
        return 'N/A'
    
    # 四舍五入到最近的半星
    rounded_rating = round(float(rating) * 2) / 2
    full_stars = int(rounded_rating)
    half_star = int((rounded_rating - full_stars) * 2)
    
    # 生成星星字符串
    star_rating = '★' * full_stars + '☆' * (5 - full_stars - half_star) + '★' * half_star
    return star_rating


def access_b23_url_and_return_real_url(url):
    res = requests.head(url, allow_redirects=True)
    real_url = res.url
    parsed_url = urlparse(real_url)
    return (
        urlunparse((parsed_url.scheme, parsed_url.netloc, parsed_url.path, "", "", "")),
        real_url,
    )

def extract_url(message: str) -> str:
    # 提取消息中的URL，忽略CQ码中的链接
    url = extract_links(message)
    print(url)
    if '[CQ:' in url:
        return None
    return url

def get_tweet_id(url):
    # 使用正则表达式从URL中提取推文ID
    match = re.search(r'/status/(\d+)', url)
    return match.group(1) if match else None

def ensure_http_scheme(url: str) -> str:
    if not url.startswith(('http://', 'https://')):
        return 'https://' + url.lstrip('//')
    return url

def truncate_repeated_chars(text):
    result = []
    count = 1
    for i in range(len(text)):
        if i > 0 and text[i] == text[i - 1]:
            count += 1
            if count <= 10:
                result.append(text[i])
        elif text[i] != text[i - 1] and text[i] == '\n':
            count = 8
            result.append(text[i])
        else:
            count = 1
            result.append(text[i])
    return ''.join(result)

async def login():
    global xlogin
    try:
        client.load_cookies('src\\plugins\\websiteshortcut\\xc.session')
        xlogin = True
    except Exception as e:
        try:
            await client.login(auth_info_1=config.X_EMAIL, auth_info_2=config.X_USERNAME, password=config.X_PASSWORD)
            xlogin = True
            client.save_cookies('src\\plugins\\websiteshortcut\\xc.session')
        except Exception as e:
            xlogin = False
            print(f"Error login x: {e}")

def fetch_dlsite_data(product_id, headers):
    url = f'https://www.dlsite.com/maniax/product/info/ajax?product_id={product_id}&cdn_cache_min=1'
    response = requests.get(url, headers=headers, proxies=proxies)
    if response.status_code == 200:
        data = response.json()
        product_data = data.get(product_id, {})
        if product_data["translation_info"]["original_workno"] != None:
            product_id = product_data["translation_info"]["original_workno"]

    url = f'https://www.dlsite.com/maniax/product/info/ajax?product_id={product_id}&cdn_cache_min=1'
    response = requests.get(url, headers=headers, proxies=proxies)
    if response.status_code == 200:
        data = response.json()
        product_data = data.get(product_id, {})
        
        total_sales = product_data['dl_count_total']
        if total_sales == 0:
            total_sales = product_data['dl_count']
        average_rating = product_data['rate_average_2dp']
        wishlist_counts = product_data['wishlist_count']
        # 生成星星字符串
        if average_rating != None:
            star_rating = generate_star_rating(average_rating)
            midresp = f"{star_rating} ({average_rating})"
        else:
            midresp = f"未发售，暂无评分。\n发售日期：{product_data['regist_date']}"

        return total_sales, midresp, wishlist_counts
    else:
        print(f"请求失败，状态码: {response.status_code}")
        return None, None, None

async def generate_dlsite_snapshot(url: str) -> tuple:
    # Extract RJ number from the input URL
    match = re.search(r'RJ\d+', url)
    if not match:
        return None, None
    
    rj_number = match.group(0)

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.88 Safari/537.36',
        'cookies': config.cookies
    }

    # 获取额外数据（销量、收藏数、评价数）
    sales, reviews, favorites = fetch_dlsite_data(rj_number, headers)
    if sales == None:
        return None, None

    dlsite_url = f'https://www.dlsite.com/maniax/work/=/product_id/{rj_number}.html?locale=zh_CN'
    
    response = requests.get(dlsite_url, headers=headers, proxies=proxies)
    if response.status_code != 200:
        return None, None
    
    soup = BeautifulSoup(response.content, 'html.parser')
    
    title = soup.find('h1', attrs={'itemprop': 'name','id': 'work_name'}).text.strip()
    if title == '无标题' or title == 'None':
        return None, None
    
    title = f"【{rj_number}】" + title

    # 尝试获取<meta>标签中的description
    description = soup.find('meta', attrs={'property': 'og:description'})
    if description and description.get('content'):
        content = truncate_repeated_chars(description['content'][:100] + '...' ) 
    else:
        content = truncate_repeated_chars(soup.get_text()[:100] + '...' )  # 获取前100个字符作为摘要

    # 获取图片 URL
    image_url = None
    og_image = soup.find('meta', attrs={'property': 'og:image'})
    if og_image and og_image.get('content'):
        image_url = og_image['content']
    
    snapshot = f"{title}\n总销量: {sales}\n收藏数: {favorites}\n评分: {reviews}\n介绍：{content}"
    image_path = None
    
    if image_url:
        image_url = ensure_http_scheme(image_url)
        image_path = await download_image(image_url, headers, 'snapshot_image.jpg')

    return snapshot, image_path

async def generate_bilibili_snapshot(url: str) -> tuple:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.88 Safari/537.36'
    }
    
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        return f"无法访问该链接，状态码: {response.status_code}", None
    
    soup = BeautifulSoup(response.content, 'html.parser')
    
    title_tag = soup.find('h1', class_='video-title')
    title = title_tag['data-title'] if title_tag and 'data-title' in title_tag.attrs else title_tag['title'] if title_tag and 'title' in title_tag.attrs else '无标题'
    description = truncate_repeated_chars(soup.find('div', class_='basic-desc-info').text if soup.find('div', class_='basic-desc-info') else '无简介')
    view_count = soup.find('div', class_='view-text').text if soup.find('div', class_='view-text') else '未知'
    danmaku_count = soup.find('div', class_='dm-text').text if soup.find('div', class_='dm-text') else '未知'
    coin_count = soup.find('span', class_='video-coin-info video-toolbar-item-text').text if soup.find('span', class_='video-coin-info video-toolbar-item-text') else '未知'
    favorite_count = soup.find('span', class_='video-fav-info video-toolbar-item-text').text if soup.find('span', class_='video-fav-info video-toolbar-item-text') else '未知'
    author = soup.find('meta', attrs={'name': 'author'})['content'] if soup.find('meta', attrs={'name': 'author'}) else '未知'
    cover_url = soup.find('meta', attrs={'property': 'og:image'})['content'] if soup.find('meta', attrs={'property': 'og:image'}) else None
    if cover_url != None:
        cover_url = cover_url[:-16] + '672w_378h_1c_!web-home-common-cover'
    
    long, original = access_b23_url_and_return_real_url(url)

    snapshot = (
        f"{title}\n"
        f"简介: \n{description}\n\n"
        f"播放量: {view_count}\n"
        f"弹幕量: {danmaku_count}\n"
        f"投币数: {coin_count}\n"
        f"收藏数: {favorite_count}\n"
        f"视频作者: {author}\n"
        f"清洁链接: \n{long}\n"
    )
    
    image_path = None
    if cover_url:
        cover_url = ensure_http_scheme(cover_url)
        image_response = requests.get(cover_url, headers=headers)
        if 'image' in image_response.headers['Content-Type']:
            try:
                image = Image.open(BytesIO(image_response.content))
                image = image.convert('RGB')  # 转换为RGB模式
                image_path = os.path.abspath(config.src_folder + 'bilibili_cover.jpg')
                image.save(image_path, format='JPEG')
            except Exception as e:
                print(f"Error processing image: {e}")
                image_path = None
    
    return snapshot, image_path

async def generate_snapshot(url: str) -> tuple:
    if url in preview_cache:
        return preview_cache[url]
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.88 Safari/537.36'
    }
    
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        return None, None
    
    soup = BeautifulSoup(response.content, 'html.parser')
    
    title = soup.title.string if soup.title else '无标题'
    if title == '无标题' or title == 'None':
        return None, None
    
    # 尝试获取<meta>标签中的description
    description = soup.find('meta', attrs={'name': 'description'})
    if description and description.get('content'):
        content = truncate_repeated_chars(description['content'][:100] + '...' ) 
    else:
        content = truncate_repeated_chars(soup.get_text()[:100] + '...' )  # 获取前100个字符作为摘要
    
    # 优先获取重要图片
    image_url = None
    og_image = soup.find('meta', attrs={'property': 'og:image'})
    twitter_image = soup.find('meta', attrs={'name': 'twitter:image'})
    meta_image = soup.find('meta', attrs={'name': 'image'})

    
    # 新增 emlog_image 处理
    emlog_image = soup.find('div', class_='post-timthumb')
    if emlog_image and 'background-image' in emlog_image.get('style', ''):
        style = emlog_image['style']
        start = style.find('url(') + 4
        end = style.find(')', start)
        image_url = style[start:end]
    
    if og_image and og_image.get('content'):
        image_url = og_image['content']
    elif twitter_image and twitter_image.get('content'):
        image_url = twitter_image['content']
    elif meta_image and meta_image.get('content'):
        image_url = meta_image['content']
    
    if not image_url:
        images = soup.find_all('img')
        if images:
            image_url = images[0]['src']
    
    snapshot = f"{title}\n摘要: \n{content}\n"
    image_path = None
    
    if image_url:
        image_url = ensure_http_scheme(image_url)
        image_path = await download_image(image_url, headers, 'snapshot_image.jpg')
    
    preview_cache[url] = (snapshot, image_path)
    
    # 如果超过限制则移除最老的10%数据
    if len(preview_cache) >= MAX_CACHE_SIZE:
        keys_to_remove = list(preview_cache.keys())[:int(MAX_CACHE_SIZE * 0.1)]
        for key in keys_to_remove:
            del preview_cache[key]

    return snapshot, image_path

# 带有重试机制的获取推文函数
async def fetch_tweet_by_id(tweet_id, retries=3):
    for attempt in range(retries):
        global xlogin
        try:
            if not xlogin:
                await login()
            tweet = await client.get_tweet_by_id(tweet_id)
            return tweet
        except httpx.ConnectTimeout:
            if attempt < retries - 1:
                await asyncio.sleep(2 ** attempt)  # 指数退避
            else:
                raise
        except httpx.HTTPStatusError as e:
            print(f"HTTP error occurred: {e.response.status_code} - {e.response.text}")
            raise

# 生成 Twitter URL 快照的函数
async def generate_x_snapshot(url: str) -> tuple:
    tweet_id = get_tweet_id(url)

    if tweet_id:
        tweet = await fetch_tweet_by_id(tweet_id)
        if tweet:
            snapshot = (
                f"推文内容: \n{tweet.text}\n\n"
                f"作者: {tweet.user.name}\n"
                f"发布时间: {tweet.created_at}\n"
            )

            media_paths = []
            if tweet.media:
                # print(tweet.media)
                i = 0
                for media in tweet.media:
                    media_url = media['media_url_https']
                    media_type = media['type']
                    if media_type == 'photo':
                        media_path = await download_media(media_url + '?format=jpg&name=4096x4096', f'tweet_image_{i}.jpg')
                        media_paths.append(media_path)
                        i += 1
                    elif media_type == 'video':
                        media_url = (media['video_info']['variants'])[0]['url']
                        if ".m3u8" in media_url:
                            media_path = os.path.abspath(config.src_folder + f'tweet_video_{i}.mp4')
                            convert_m3u8_to_mp4(media_url, media_path)
                        else:
                            media_path = await download_media(media_url, f'tweet_video_{i}.mp4')
                        media_paths.append(media_path)
                        i += 1
                    elif media_type == 'animated_gif':
                        media_url = (media['video_info']['variants'])[0]['url']
                        media_path = await download_media(media_url, f'tweet_video_{i}.mp4')
                        media_paths.append(media_path)
                        i += 1

            return snapshot, media_paths
        else:
            return "未找到推文", None
    else:
        return "无效的推文URL", None

# 从 URL 下载媒体的函数
async def download_media(url: str, filename: str) -> str:
    try:
        response = requests.get(url)
        if response.status_code == 200:
            media_path = os.path.abspath(config.src_folder + filename)
            with open(media_path, 'wb') as file:
                file.write(response.content)
            return media_path
        else:
            print(f"Failed to download media, status code: {response.status_code}")
            return None
    except Exception as e:
        print(f"Error downloading media: {e}")
        return None

async def download_image(url: str, headers: dict, filename: str) -> str:
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200 and 'image' in response.headers['Content-Type']:
            image = Image.open(BytesIO(response.content))
            image = image.convert('RGB')  # 转换为RGB模式
            image_path = os.path.abspath(config.src_folder + filename)
            image.save(image_path, format='JPEG')
            return image_path
        else:
            print(f"Invalid image MIME type or status code: {response.status_code}, {response.headers['Content-Type']}")
            return None
    except:
        try:
            response = requests.get(url, headers=headers, proxies=proxies)
            if response.status_code == 200 and 'image' in response.headers['Content-Type']:
                image = Image.open(BytesIO(response.content))
                image = image.convert('RGB')  # 转换为RGB模式
                image_path = os.path.abspath(config.src_folder + filename)
                image.save(image_path, format='JPEG')
                return image_path
            else:
                print(f"Invalid image MIME type or status code: {response.status_code}, {response.headers['Content-Type']}")
                return None
        except Exception as e:
            print(f"Error processing image: {e}")
            return None

async def image_url_to_base64(url: str, headers: dict) -> str:
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return f"data:image/jpeg;base64,{base64.b64encode(response.content).decode('utf-8')}"
        else:
            print(f"Failed to fetch image, status code: {response.status_code}")
            return None
    except Exception as e:
        print(f"Error fetching image: {e}")
        return None

xlogin = False
asyncio.run(login())

def convert_m3u8_to_mp4(m3u8_file, output_file):
    command = [
        'ffmpeg',
        '-y',
        '-i', m3u8_file,
        '-c', 'copy',
        output_file
    ]
    subprocess.run(command)
