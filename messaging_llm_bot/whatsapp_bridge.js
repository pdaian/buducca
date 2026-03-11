#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const process = require("process");
const qrcode = require("qrcode-terminal");
const { Client, LocalAuth, MessageMedia } = require("whatsapp-web.js");

function printUsage() {
  console.error(
    "Usage: node messaging_llm_bot/whatsapp_bridge.js <pair|receive|send> [options]\n" +
      "Commands:\n" +
      "  pair --session <dir>\n" +
      "  receive --session <dir> [--state-file <path>] [--media-dir <path>]\n" +
      "  send --session <dir> --recipient <id> [--message <text>] [--attachment <path>]"
  );
}

function parseArgs(argv) {
  if (argv.length === 0) {
    printUsage();
    process.exit(2);
  }
  const command = argv[0];
  const options = {
    session: "data/whatsapp-default",
    stateFile: null,
    mediaDir: null,
    recipient: "",
    message: "",
    attachment: "",
    headless: true,
    browserPath: "",
    readyTimeoutSeconds: 45,
  };

  for (let index = 1; index < argv.length; index += 1) {
    const key = argv[index];
    const value = argv[index + 1];
    if (key === "--headful") {
      options.headless = false;
      continue;
    }
    if (key === "--headless") {
      options.headless = value !== "false";
      index += 1;
      continue;
    }
    if (!key.startsWith("--")) {
      throw new Error(`Unexpected argument: ${key}`);
    }
    if (value === undefined || value.startsWith("--")) {
      throw new Error(`Missing value for ${key}`);
    }
    switch (key) {
      case "--session":
        options.session = value;
        break;
      case "--state-file":
        options.stateFile = value;
        break;
      case "--media-dir":
        options.mediaDir = value;
        break;
      case "--recipient":
        options.recipient = value;
        break;
      case "--message":
        options.message = value;
        break;
      case "--attachment":
        options.attachment = value;
        break;
      case "--browser-path":
        options.browserPath = value;
        break;
      case "--ready-timeout-seconds":
        options.readyTimeoutSeconds = Number(value);
        break;
      default:
        throw new Error(`Unknown option: ${key}`);
    }
    index += 1;
  }

  if (!Number.isFinite(options.readyTimeoutSeconds) || options.readyTimeoutSeconds <= 0) {
    throw new Error("--ready-timeout-seconds must be > 0");
  }

  return { command, options };
}

function ensureDir(targetPath) {
  fs.mkdirSync(targetPath, { recursive: true });
}

function resolvePath(targetPath) {
  return path.resolve(process.cwd(), targetPath);
}

function sanitizeName(value) {
  return value.replace(/[^a-zA-Z0-9_-]+/g, "-").replace(/^-+|-+$/g, "") || "default";
}

function sessionInfo(sessionPath) {
  const absoluteSessionPath = resolvePath(sessionPath);
  const parentDir = path.dirname(absoluteSessionPath);
  const clientId = sanitizeName(path.basename(absoluteSessionPath));
  ensureDir(parentDir);
  ensureDir(absoluteSessionPath);
  return {
    absoluteSessionPath,
    parentDir,
    clientId,
  };
}

function defaultStateFile(sessionPath) {
  return `${resolvePath(sessionPath)}.receive-state.json`;
}

function defaultMediaDir(sessionPath) {
  return `${resolvePath(sessionPath)}-media`;
}

function loadState(stateFile) {
  try {
    return JSON.parse(fs.readFileSync(stateFile, "utf8"));
  } catch (error) {
    return { seen_message_ids: [] };
  }
}

function saveState(stateFile, state) {
  ensureDir(path.dirname(stateFile));
  fs.writeFileSync(stateFile, JSON.stringify(state, null, 2));
}

function withTimeout(promise, ms, label) {
  let timer = null;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => {
    if (timer) {
      clearTimeout(timer);
    }
  });
}

function createClient(options) {
  const { parentDir, clientId } = sessionInfo(options.session);
  const puppeteer = {
    headless: options.headless,
    args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
  };
  if (options.browserPath) {
    puppeteer.executablePath = resolvePath(options.browserPath);
  }
  return new Client({
    authStrategy: new LocalAuth({
      clientId,
      dataPath: parentDir,
    }),
    puppeteer,
  });
}

function waitForReady(client, timeoutMs) {
  return withTimeout(
    new Promise((resolve, reject) => {
      let qrPrinted = false;
      client.on("qr", (qrValue) => {
        qrPrinted = true;
        qrcode.generate(qrValue, { small: true });
        console.error("Scan the QR in WhatsApp -> Settings -> Linked Devices -> Link a Device");
      });
      client.on("authenticated", () => {
        console.error("WhatsApp authenticated.");
      });
      client.on("ready", () => {
        if (!qrPrinted) {
          console.error("WhatsApp session already linked.");
        }
        resolve();
      });
      client.on("auth_failure", (message) => {
        reject(new Error(`WhatsApp authentication failed: ${message || "unknown error"}`));
      });
      client.on("disconnected", (reason) => {
        reject(new Error(`WhatsApp disconnected before ready: ${reason || "unknown reason"}`));
      });
      client.initialize().catch(reject);
    }),
    timeoutMs,
    "WhatsApp session initialization"
  );
}

function normalizePhoneId(raw) {
  const digits = String(raw || "")
    .trim()
    .replace(/[^+\d]/g, "");
  if (!digits) {
    return "";
  }
  return `${digits.replace(/^\+/, "")}@c.us`;
}

function conversationIdForChat(chat) {
  if (chat.isGroup) {
    const name = String(chat.name || "group").replace(/\|/g, "/").trim() || "group";
    return `group:${name}|${chat.id._serialized}`;
  }
  return chat.id._serialized;
}

function senderIdForMessage(message, chat) {
  if (chat.isGroup) {
    return message.author || message.from || "";
  }
  return message.from || chat.id._serialized;
}

function sentAtForMessage(message) {
  if (!message.timestamp) {
    return null;
  }
  return new Date(message.timestamp * 1000).toISOString();
}

function mediaFilename(message, extension) {
  const baseName = message.id && message.id.id ? message.id.id : `media-${Date.now()}`;
  return `${baseName}.${extension || "bin"}`;
}

async function downloadAttachment(message, mediaDir) {
  if (!message.hasMedia) {
    return null;
  }
  const media = await message.downloadMedia();
  if (!media || !media.data) {
    return null;
  }
  ensureDir(mediaDir);
  const extension = media.mimetype && media.mimetype.includes("/")
    ? media.mimetype.split("/")[1].split(";")[0]
    : "bin";
  const filename = media.filename || mediaFilename(message, extension);
  const filePath = path.join(mediaDir, filename);
  fs.writeFileSync(filePath, Buffer.from(media.data, "base64"));
  return {
    path: filePath,
    name: filename,
    mime_type: media.mimetype || "application/octet-stream",
  };
}

async function commandPair(options) {
  const client = createClient(options);
  try {
    await waitForReady(client, options.readyTimeoutSeconds * 1000);
    console.error(`Linked session stored under ${resolvePath(options.session)}`);
  } finally {
    await client.destroy().catch(() => {});
  }
}

async function commandReceive(options) {
  const client = createClient(options);
  const stateFile = resolvePath(options.stateFile || defaultStateFile(options.session));
  const mediaDir = resolvePath(options.mediaDir || defaultMediaDir(options.session));
  const state = loadState(stateFile);
  const seen = new Set(Array.isArray(state.seen_message_ids) ? state.seen_message_ids : []);

  try {
    await waitForReady(client, options.readyTimeoutSeconds * 1000);
    const chats = await client.getChats();
    const messages = [];

    for (const chat of chats) {
      const unreadCount = Number(chat.unreadCount || 0);
      if (unreadCount <= 0) {
        continue;
      }
      const recent = await chat.fetchMessages({ limit: Math.max(unreadCount, 20) });
      const unreadMessages = recent.filter((message) => !message.fromMe);
      for (const message of unreadMessages) {
        const messageId = message.id && message.id._serialized ? message.id._serialized : "";
        if (!messageId || seen.has(messageId)) {
          continue;
        }
        const attachment = await downloadAttachment(message, mediaDir);
        const text = typeof message.body === "string" ? message.body.trim() : "";
        if (!text && !attachment) {
          seen.add(messageId);
          continue;
        }
        const contact = await message.getContact();
        messages.push({
          conversation_id: conversationIdForChat(chat),
          sender_id: senderIdForMessage(message, chat),
          sender_name: contact.pushname || contact.name || contact.shortName || null,
          sender_contact: contact.number || contact.pushname || contact.name || senderIdForMessage(message, chat),
          text: text || null,
          sent_at: sentAtForMessage(message),
          attachments: attachment ? [attachment] : [],
        });
        seen.add(messageId);
      }
    }

    const seenList = Array.from(seen);
    state.seen_message_ids = seenList.slice(-2000);
    saveState(stateFile, state);
    process.stdout.write(JSON.stringify({ messages }));
  } finally {
    await client.destroy().catch(() => {});
  }
}

async function resolveRecipient(client, recipient) {
  const trimmed = String(recipient || "").trim();
  if (!trimmed) {
    throw new Error("--recipient is required");
  }
  if (trimmed.startsWith("group:") && trimmed.includes("|")) {
    return trimmed.split("|").slice(-1)[0];
  }
  if (trimmed.includes("@")) {
    return trimmed;
  }
  const directId = normalizePhoneId(trimmed);
  if (!directId) {
    throw new Error(`Unsupported recipient value: ${trimmed}`);
  }
  const numberId = await client.getNumberId(trimmed.replace(/[^\d]/g, ""));
  if (numberId && numberId._serialized) {
    return numberId._serialized;
  }
  return directId;
}

async function commandSend(options) {
  if (!options.recipient) {
    throw new Error("--recipient is required");
  }
  if (!options.message && !options.attachment) {
    throw new Error("either --message or --attachment is required");
  }
  const client = createClient(options);
  try {
    await waitForReady(client, options.readyTimeoutSeconds * 1000);
    const chatId = await resolveRecipient(client, options.recipient);
    if (options.attachment) {
      const attachmentPath = resolvePath(options.attachment);
      if (!fs.existsSync(attachmentPath)) {
        throw new Error(`Attachment file not found: ${attachmentPath}`);
      }
      const media = MessageMedia.fromFilePath(attachmentPath);
      await client.sendMessage(chatId, media, { caption: options.message || "" });
      return;
    }
    await client.sendMessage(chatId, options.message);
  } finally {
    await client.destroy().catch(() => {});
  }
}

async function main() {
  try {
    const { command, options } = parseArgs(process.argv.slice(2));
    if (command === "pair") {
      await commandPair(options);
      return;
    }
    if (command === "receive") {
      await commandReceive(options);
      return;
    }
    if (command === "send") {
      await commandSend(options);
      return;
    }
    throw new Error(`Unknown command: ${command}`);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error(message);
    process.exit(1);
  }
}

main();
