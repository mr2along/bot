# AI Council V2

Web App + Hugging Face WebSocket relay + Chrome agents for ChatGPT/Copilot.

## HF
Upload `hf/` to a Docker Space. Endpoint: `wss://YOUR-SPACE.hf.space/ws`

## Web
Open `web/index.html`, enter the WebSocket URL and room ID, then CONNECT.

## Extension
Load `extension/` as an unpacked Chrome extension. Configure one tab as `chatgpt` and one as `copilot`, using the same WebSocket URL and room ID.

The relay only forwards messages. It does not store credentials or call ChatGPT/Copilot APIs. DOM selectors may require updates if either website changes its UI.
