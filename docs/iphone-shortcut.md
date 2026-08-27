# DeckClip Save to Photos shortcut

DeckClip can pass a temporary, single-file download URL directly to an iPhone Shortcut. The URL is never copied to the clipboard or saved by DeckClip.

## Create the shortcut

1. Open **Shortcuts** on the iPhone and create a new shortcut.
2. Name it exactly **DeckClip Save to Photos**.
3. Add **Get Contents of URL**. Set its URL to **Shortcut Input** and leave the method as `GET`.
4. Add **Save to Photo Album** after it. Use the result from **Get Contents of URL** and select the desired album, such as Recents.
5. Run it once with a test URL if iOS needs to request network or Photos permission. Review the two actions before granting access.

## Use it

1. Export a clip and choose **Send to phone** in DeckClip.
2. Scan the QR code on the iPhone.
3. Tap **Save to Photos** on the temporary DeckClip page.
4. Shortcuts downloads that one clip and asks iOS to save it to Photos.

If the Shortcut is not installed or fails, use **Download to Files** on the same page.

## Security model

- The Shortcut receives only the current temporary URL as text input.
- It does not use the clipboard, store a Deck address, or keep a pairing secret.
- The URL contains a random bearer token, expires after ten minutes, and the server closes 30 seconds after a complete download.
- The transfer uses unencrypted local HTTP. Use it only on a trusted private network.
- Anyone who can read the temporary URL while it is active can retrieve that clip. Do not share screenshots of the QR code.
- iOS controls network and Photos permission. Access can be reviewed or revoked in the Shortcut's privacy settings.
