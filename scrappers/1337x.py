from bs4 import BeautifulSoup
import requests
import json
from flask import Flask, request, Response

sitesAvailable = [{"id": 1, "name": "1337x"}]

home = [
    {"route_id": 1, "route_name": "Home", "route_url": "/"},
    {"route_id": 2, "route_name": "Torrent List", "route_url": "/torrents"},
    {"route_id": 3, "route_name": "Torrent Data", "route_url": "/magnet"},
    {"route_id": 4, "route_name": "Sites", "route_url": "/sites"}
]


def getTorrentsList(search_key):
    url="https://www.1377x.to/search/" + search_key + "/1/"
    response = requests.get(url, verify=False)
    data = []
    soup = BeautifulSoup(response.text, "lxml")
    table_body = soup.find("tbody")
    rows = table_body.find_all("tr")
    if len(rows) > 10:
        length = 10
    else:
        length = len(rows)
    for i in range(0, length):
        cols = rows[i].find_all("td")
        col1 = cols[0].find_all("a")[1]
        name = col1.text
        url = "https://www.1377x.to/" + col1['href']
        data.append(
            {
                "name": name,
                "url": url,
                "seeds": cols[1].text,
                "leeches": cols[2].text,
                "date": cols[3].text,
                "size": cols[4].text,
                "uploader": cols[5].text,
            }
        )
    return data


def gettorrentdata(link):
    response = requests.get(link, verify=False)
    files = []
    data = {}
    soup = BeautifulSoup(response.text, "lxml")
    magnet = soup.find("a", {"class": "l3426749b3b895e9356348e295596e5f2634c98d8"})
    magnet = magnet["href"]
    div = soup.find("div", {"class": "file-content"})
    lis = div.find_all("li")
    for l in lis:
        files.append(str.strip(l.text))
    data["magnet"] = magnet
    data["files"] = files
    return data


app = Flask(__name__)

@app.route("/", methods=["GET"])
def index():
    return Response(json.dumps(home), mimetype="application/json")

@app.route("/sites", methods=["GET"])
def getSites():
    return Response(json.dumps(sitesAvailable), mimetype="application/json")


@app.route("/torrents", methods=["GET"])
def getTorrents():
    search_key = request.args.get("key")
    if search_key is None or search_key == "":
        return Response(json.dumps([]))
    return Response(
        json.dumps(getTorrentsList(search_key)), mimetype="application/json"
    )

@app.route("/magnet", methods=["GET"])
def getTorrentData():
    link = request.args.get("link")
    if link is None or link == "":
        return Response(json.dumps([]))
    return Response(json.dumps(gettorrentdata(link)), mimetype="application/json")


if __name__ == "__main__":
    app.run(debug=True)
