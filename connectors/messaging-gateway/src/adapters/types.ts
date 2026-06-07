export type InboundHandler = (
  chatId: string,
  sender: string,
  text: string
) => Promise<void>;

export interface Adapter {
  /** Canonical platform name, e.g. "whatsapp", "slack", "discord". */
  readonly platform: string;
  /**
   * Start the adapter. `onInbound` is called for each message received
   * from end-users that should be forwarded to the backend.
   */
  start(onInbound: InboundHandler): Promise<void>;
  /** Send a text message to the given recipient/channel/jid. */
  sendMessage(recipient: string, text: string): Promise<void>;
}
