# TerminalText

A green, split-screen terminal inbox for short SMS and email conversations with explicitly whitelisted contacts.

## Free service model

- **Email:** any mailbox that provides IMAP and SMTP access. TerminalText polls IMAP over TLS every 15 seconds, imports plain text only, and sends through SMTP over TLS. Add contacts first: mail from every other sender is ignored.
- **SMS:** TerminalText sends through a paired Android phone using KDE Connect and the phone's existing carrier/SIM plan. KDE Connect is free software, but carrier SMS can have charges. SMS receipt notifications depend on the desktop and phone notification settings; the KDE Connect command line does not provide a stable SMS inbox API, so full inbound SMS history is not supported here.
- **Calls:** opens the system `tel:` handler, if one is configured.
- **Attachments:** never opened or saved. Email messages are reduced to plain text, up to 500 characters.

There is no reliable free, standalone phone number included. A phone number, carrier service, and Android phone are needed for phone SMS. For email-only use, no phone is needed.

## Run from a checkout

Requires Python 3.9+ and a real terminal. SMS needs KDE Connect desktop and its SMS plugin enabled on a paired Android device. Install with your distro package manager (for example `sudo apt install kdeconnect` on Debian/Ubuntu).

```bash
./terminaltext.sh
```

Keys: `1` add a contact, `2` email a contact, `3` text a contact, `4` call a contact, `5` configure email, `6` refresh, `q` quit. Type `:` then `...` to quit. The interface also displays `Type ... to quit` in the command pane.

The exact loading interval is 1.19 seconds; terminal refresh timing limits how precisely individual cat frames can appear on screen.

## Install from GitHub

After this project is pushed to GitHub:

```bash
curl -fsSL https://raw.githubusercontent.com/williamkbukowski94/terminaltext/main/install.sh -o install.sh
bash install.sh
```

The installer installs Git, Python 3, KDE Connect, and xdg-utils with apt, dnf, pacman, or zypper when needed. It installs TerminalText under `~/.local/share` and adds a launcher under `~/.local/bin`. Pair the Android phone in KDE Connect and enable its SMS plugin before sending texts.

## Privacy

Configuration and displayed message cache are stored in `~/.config/terminaltext/` with owner-only file permissions. The email password is an app password where the mailbox provider supports one. No third-party server operated by TerminalText receives message content.
