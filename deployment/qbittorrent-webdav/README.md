# qBittorrent-WebDAV Docker Setup for MediaFusion

Stream torrents through Stremio with ease using the qBittorrent-WebDAV Docker image, offering a more controlled way to download torrents and stream content via Stremio.


## Creating a .htpasswd File (Optional)

To add password protection to WebDAV, you need to create a `.htpasswd` file containing user credentials.

### Using the `htpasswd` Command

If you have access to the Apache `htpasswd` command, you can generate the `.htpasswd` file locally:

```bash
htpasswd -cBm .htpasswd username
```

Replace `username` with your desired username. You will be prompted to enter and confirm a password. The resulting `.htpasswd` file will contain your hashed password, which can be used with WebDAV for authentication.

### Using an Online Generator

Alternatively, you can use an online `.htpasswd` generator to create the MD5 hash for your password. Visit a website like [htpasswd Generator](https://htpasswd.org) or [htpasswd Generator](https://hostingcanada.org/htpasswd-generator/) (ensure you select the MD5 algorithm for Apache compatibility), enter your username and password, and generate the hash.

Once generated, copy the Apache MD5 hash and create a `.htpasswd` file manually with the content.

## Running the Docker Image

Launch qBittorrent-WebDAV using:

```bash
docker run -d \
  --name=qbittorrent-webdav \
  -p 8080:80 \
  -p 6881:6881/tcp \
  -p 6881:6881/udp \
  -v /path/to/htpasswd:/etc/apache2/.htpasswd \ # Optional: For WebDAV password protection
  -v /path/to/downloads:/downloads \
  mhdzumair/qbittorrent-webdav:latest
```

Adjust `/path/to/htpasswd` to the path where you'll store the `.htpasswd` file if you want to set up password for WebDav, and `/path/to/downloads` to your desired downloads directory.

## Initial qBittorrent and WebDAV Setup

**qBittorrent WebUI:** Access at `http://localhost:8080/qbittorrent/`. Find the initial password in the Docker logs:

```bash
docker logs qbittorrent-webdav
```

**WebDAV:** Found at `http://localhost/webdav/`. If you created a `.htpasswd` file, use those credentials. If not, WebDAV is open.

## Integration with MediaFusion

Configure MediaFusion:

1. Open MediaFusion config page.
2. Select "qBittorrent - WebDav" as the streaming provider.
3. Fill in WebUI and WebDAV URLs (use local addresses for a local setup).
4. Enter WebDAV credentials if set; leave blank if not.
5. Adjust seeding settings and 'Play Video After Download' percentage.
6. Save and start streaming through Stremio.

Refer to the screenshot below for a visual guide:
![MediaFusion qBittorrent-WebDAV Configuration](/deployment/qbittorrent-webdav/ss.png)

## Local vs. Community Hosted MediaFusion

- **Local Host:** Run both MediaFusion and qBittorrent-WebDAV on your local network for personal streaming.
- **Community Hosted MediaFusion:** Pair a publicly hosted qBittorrent-WebDAV server with the community version of the MediaFusion Stremio addon.

> [!WARNING]
> **Important:** When hosting services publicly, prioritize security. Use strong passwords, HTTPS to work with stremio.

## Help and Contributions

For support, issues, or contributions, interact through the GitHub repository. We welcome your input to improve this project for all users.
