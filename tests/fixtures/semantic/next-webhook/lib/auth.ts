import GitHubProvider from "next-auth/providers/github";
import EmailProvider from "next-auth/providers/email";
import { Client } from "postmark";

const postmarkClient = new Client(process.env.POSTMARK_API_TOKEN!);

export const authOptions = {
  providers: [
    GitHubProvider({
      clientId: process.env.GITHUB_CLIENT_ID!,
      clientSecret: process.env.GITHUB_CLIENT_SECRET!,
    }),
    EmailProvider({
      from: process.env.SMTP_FROM,
      sendVerificationRequest: async ({ identifier }) => {
        const templateId = identifier
          ? process.env.POSTMARK_SIGN_IN_TEMPLATE
          : undefined
        await postmarkClient.sendEmailWithTemplate({
          TemplateId: parseInt(templateId!),
          To: identifier,
        });
      },
    }),
  ],
};
