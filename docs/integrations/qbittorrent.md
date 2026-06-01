# qBittorrent WebDAV Integration

Stream torrents through Stremio by running your own qBittorrent instance with a WebDAV server. MediaFusion downloads torrents to qBittorrent and streams the files over WebDAV.

## Prerequisites

Docker installed on the machine that will run qBittorrent.

## Step 1: (Optional) Create a WebDAV password file

To password-protect WebDAV access:

=== "Using htpasswd command"

    ```bash
    htpasswd -cBm .htpasswd your_username
    # Enter and confirm your password when prompted
    ```

=== "Using an online generator"

    Generate an MD5 hash at a site like [htpasswd.org](https://htpasswd.org), then create a file manually:

    ```
    username:$apr1$...hash...
    ```

## Step 2: Run the Docker image

```bash
docker run -d \
  --name=qbittorrent-webdav \
  --restart unless-stopped \
  -p 8080:80 \
  -p 6881:6881/tcp \
  -p 6881:6881/udp \
  -v /path/to/downloads:/downloads \
  mhdzumair/qbittorrent-webdav:latest
```

With WebDAV password protection:
```bash
docker run -d \
  --name=qbittorrent-webdav \
  --restart unless-stopped \
  -p 8080:80 \
  -p 6881:6881/tcp \
  -p 6881:6881/udp \
  -v /path/to/.htpasswd:/etc/apache2/.htpasswd \
  -v /path/to/downloads:/downloads \
  mhdzumair/qbittorrent-webdav:latest
```

## Step 3: Get the initial qBittorrent password

```bash
docker logs qbittorrent-webdav
```

Look for a line like `The WebUI administrator password was not set. A temporary password is provided for this session: xxxxxxxx`.

Access the qBittorrent WebUI at `http://localhost:8080/qbittorrent/` with username `admin` and the temporary password.

WebDAV is available at `http://localhost:8080/webdav/`.

## Step 4: Configure MediaFusion

1. Open MediaFusion → **Configure** → **Streaming Provider** → select **qBittorrent – WebDav**
2. Get the container's IP address (needed if MediaFusion is also running in Docker):
   ```bash
   docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' qbittorrent-webdav
   ```
3. Fill in the URLs:
   - **MediaFusion running locally** (not in Docker): use `http://localhost:8080`
   - **MediaFusion running in Docker**: use `http://<container_ip>:8080`
4. Enter your WebDAV credentials if you set a password; leave blank if not
5. Adjust the **Play Video After Download** percentage (e.g. `30` = start playing when 30% is downloaded)
6. Save the configuration

!!! warning "Public hosting security"
    If you expose qBittorrent-WebDAV publicly, always use a strong WebDAV password and HTTPS.

## Local vs. community-hosted MediaFusion

- **Local**: run both MediaFusion and qBittorrent-WebDAV on the same machine or LAN
- **Community-hosted**: expose qBittorrent-WebDAV publicly (with HTTPS and authentication) and connect it to the community MediaFusion instance
