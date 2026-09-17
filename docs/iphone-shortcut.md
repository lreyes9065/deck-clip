# DeckClip Save to Photos shortcut

DeckClip can pass one temporary manifest URL to an iPhone Shortcut. The manifest contains the selected clip names and their temporary download URLs; it never contains filesystem paths. The URL is never copied to the clipboard or saved by DeckClip.

## Create the shortcut

Open DeckClip's permanent **Help & Settings** screen before starting a transfer. The same instructions are included there, so the ten-minute transfer timer does not run while the Shortcut is being created.

1. Open **Shortcuts** on the iPhone, tap **+**, and rename the new shortcut exactly **DeckClip Save to Photos**.
2. Search for and add **Get Contents of URL**. Tap its URL field, choose **Select Variable**, and select **Shortcut Input**.
3. Add **Get Dictionary Value**. Enter `files` as the key. It should read “Get Value for files in Contents of URL.”
4. Add **Repeat with Each**. It should use **Dictionary Value** from the preceding action.
5. Inside the Repeat section, add **Get Dictionary Value**. Enter `url` as the key and select **Repeat Item** as its input.
6. Still inside Repeat, add **Get Contents of URL**. It should use the preceding **Dictionary Value**.
7. Still inside Repeat, add **Save to Photo Album**. It must use the immediately preceding **Contents of URL**, not Shortcut Input.
8. Confirm those final three actions appear above **End Repeat**. Run the shortcut from DeckClip so iOS can request local-network and Photos permission.

## Use it

1. Open **Manage exported clips**, select up to 20 clips, and choose **Send selected clips**.
2. Scan the QR code on the iPhone.
3. Tap **Save all to Photos** on the temporary DeckClip page.
4. Shortcuts downloads each selected clip and saves it to Photos.

If the Shortcut is not installed or fails, use the individual download links on the same page.

## Security model

- The Shortcut receives only the current temporary manifest URL as text input.
- It does not use the clipboard, store a Deck address, or keep a pairing secret.
- The URL contains a random bearer token, expires after ten minutes, and the server closes 30 seconds after every selected file has completed a full download. A completed HTTP download does not prove Photos saved it.
- The transfer uses unencrypted local HTTP. Use it only on a trusted private network.
- Anyone who can read the temporary URL while it is active can retrieve every clip in that batch. Do not share screenshots of the QR code.
- iOS controls network and Photos permission. Access can be reviewed or revoked in the Shortcut's privacy settings.
