import { Queue } from "bullmq";

export const receiptQueue = new Queue("receipts");
