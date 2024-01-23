import requests
from bs4 import BeautifulSoup
import pyperclip
import pprint

def yify(query):
    response = requests.get("https://yts.unblockit.date/movies/" + query)
    soup = BeautifulSoup(response.text, "html.parser")
    torrents = soup.select(".magnet")
    size = soup.select(".quality-size")
    qs = [i.getText(".quality-size") for i in size]
    qua, si = qs[::2], qs[1::2]
    data = []

    for index, item in enumerate(torrents):
        title = item.get("title")[9:]
        magnet = item.get("href")
        quality = qua[index]
        size = si[index]
        data.append({"Index" : index+1 ,"Title" : title, "Magnet" : magnet, "Quality" : quality, "Size" : size})
    return data
