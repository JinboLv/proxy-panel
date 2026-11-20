# PROXY Panel

A Chinese-language web panel for managing mihomo on an Ubuntu server. Use it to import proxy nodes and subscriptions, switch the active node, run connectivity and download tests, and view logs. The panel runs on the server and opens in your local browser through SSH forwarding.

## Download and install

Run these commands on the **Ubuntu server**. The launcher uses the system Python at `/usr/bin/python3` and requires Python 3.10 or later, PyYAML, and a Linux kernel with `pidfd_open` support.

1. Install the dependencies. Skip this step if they are already installed.

   ```bash
   sudo apt update
   sudo apt install git curl tmux python3 python3-yaml gzip
   ```

2. On the GitHub repository page, select **Code**, choose **HTTPS**, and copy the clone URL. Paste it at the prompt below. The project will be cloned into `~/proxy-panel`.

   ```bash
   read -r -p 'GitHub HTTPS clone URL: ' repo_url
   git clone "$repo_url" "$HOME/proxy-panel"
   ```

3. Download the Linux `.gz` archive for your server from the [official mihomo releases](https://github.com/MetaCubeX/mihomo/releases). Run `uname -m` to check the architecture. Use `amd64` for `x86_64` or `arm64` for `aarch64`. Replace `/path/to/mihomo-linux.gz` with the path to the downloaded archive.

   ```bash
   mkdir -p "$HOME/bin"
   gzip -dc /path/to/mihomo-linux.gz > "$HOME/bin/mihomo"
   chmod 755 "$HOME/bin/mihomo"
   "$HOME/bin/mihomo" -v
   ```

   The launcher looks for `~/bin/mihomo` by default. If you already have mihomo installed elsewhere, skip the download and pass its executable path, for example `~/proxy-panel/start.sh --mihomo /usr/local/bin/mihomo`.

## Configure SSH on your computer

Edit `~/.ssh/config` on Linux or macOS, or `%USERPROFILE%\.ssh\config` with Windows OpenSSH. Replace `alice`, the example IP addresses, and the server home directory with your own values. The socket in `LocalForward` must be an absolute path **on the server**. Run `echo "$HOME"` on the server if you are unsure of the path.

### Direct connection

```sshconfig
Host proxy-panel
    HostName 203.0.113.10
    User alice
    ExitOnForwardFailure yes
    LocalForward 127.0.0.1:18080 /home/alice/.local/share/proxy-panel/panel.sock
```

### Connection through a jump host

If you already have a working SSH alias, add `ExitOnForwardFailure` and `LocalForward` to the **target server's** `Host` block. Keep its existing connection, jump host, and key settings. Here is a complete example for a new alias.

```sshconfig
Host example-jump
    HostName 198.51.100.20
    User jumpuser

Host proxy-panel
    HostName 203.0.113.10
    User alice
    ProxyJump example-jump
    ExitOnForwardFailure yes
    LocalForward 127.0.0.1:18080 /home/alice/.local/share/proxy-panel/panel.sock
```

Choose one of the two configurations. On your computer, run `ssh proxy-panel` or use the existing alias you updated. Once connected, follow the next section to start the panel.

If the browser reports that the connection was refused, check that you used the alias with `LocalForward`, the panel is running on the server, and no other program is using local port `18080`. The SSH server must allow Unix socket forwarding. The panel listens on a private Unix socket, so there is no need to open server port `18080` to the network.

## Start and stop

After connecting with SSH forwarding, run this command in your server terminal.

```bash
~/proxy-panel/start.sh
```

The script starts PROXY or reconnects to its existing tmux session. It prints `http://127.0.0.1:18080`. Keep the SSH session open and visit that address in your local browser. SSH handles authentication, so the panel opens without a separate login.

| Action | What to do |
| --- | --- |
| Stop PROXY and its mihomo instance | Press `Ctrl+C` in the PROXY terminal, or run `~/proxy-panel/stop.sh` from another server terminal |
| Detach while keeping the proxy running | Press `Ctrl+B`, release both keys, then press `D` |
| Return to the PROXY terminal | Run `~/proxy-panel/start.sh` again |

Closing the browser or disconnecting SSH leaves the proxy running in tmux. After rebooting the server, run the start script again.

## Import and use proxies

- Use the import button in the upper right to paste node links or Clash YAML. To add a subscription, open the subscriptions page, add the URL, and refresh it.
- Select a node to route new connections through it. Tests run in temporary mihomo instances. Connectivity tests run up to eight at a time, and download tests run up to two at a time.
- To use the proxy from a Bash shell on the server, run `source ~/proxy-panel/use-proxy.sh`. This loads the proxy address and credentials into that shell. Programs launched from it inherit those settings.
- The browser panel uses port `18080` on your computer. The proxy itself listens on `127.0.0.1:17891` by default on the server, supports HTTP and SOCKS5, and requires authentication.

## Data and backups

Runtime data lives in `~/.local/share/proxy-panel/` on the server. Keep this directory out of Git. It may contain node credentials, subscription URLs, generated passwords, and logs. Review files and commit history for personal information before publishing them.
