# qBittorrent-WebDAV Setup Options with MediaFusion

This guide provides instructions on how to set up qBittorrent with WebDAV for use with MediaFusion. Users can choose between self-hosted setups or managed services from ElfHosted, with configuration options for each.

## Option 1: Self-Hosted Setup

For detailed instructions on setting up qBittorrent and WebDAV in a Docker container on your local network, refer to the documentation [qbittorrent-webdav](../../deployment/qbittorrent-webdav/README.md).

## Option 2: ElfHosted Services Setup

For users preferring a managed service, ElfHosted offers two configuration options:

### Configuration Options:

1. ElfHosted MediaFusion Subscribed service.
   - [MediaFusion Addon](https://store.elfhosted.com/product/mediafusion)
   - [qBittorrent](https://store.elfhosted.com/product/qbittorrent)
   - [WebDAV](https://store.elfhosted.com/product/webdav) / [WebDav Plus](https://store.elfhosted.com/product/webdav-access-plus)
2. ElfHosted MediaFusion Community service.
   - [MediaFusion Community Addon](https://mediafusion.elfhosted.com/)
   - [qBittorrent](https://store.elfhosted.com/product/qbittorrent)
   - [Exposed qBittorrent](https://store.elfhosted.com/product/qbittorrent-exposed)
   - [WebDAV](https://store.elfhosted.com/product/webdav) / [WebDav Plus](https://store.elfhosted.com/product/webdav-access-plus)

### Subscription and Configuration:

To use the ElfHosted services, follow these steps:

1. **Subscribe to ElfHosted Services**: Choose your desired service configuration option from above.
2. **Configure MediaFusion Addon**: Once subscribed, configure your MediaFusion addon according to the following screenshot.
   > [!TIP]
   > - For option 1, use the qbittorrent URL as "http://qbittorrent:8080".
   > - For option 2, when exposed the qbittorrent, make sure to set webui password and disable the bypass authentication option in the qbittorrent settings.

   ![MediaFusion qBittorrent-WebDAV Configuration](/deployment/qbittorrent-webdav/ss.png)
