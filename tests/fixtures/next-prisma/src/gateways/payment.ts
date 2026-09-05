export async function createPayment(amount: number) {
  return fetch("https://api.example-pay.test/v1/payments", { method: "POST" });
}
