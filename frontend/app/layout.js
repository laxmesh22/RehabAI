import './globals.css';

export const metadata = {
  title: 'RehabAI',
  description: 'AI-assisted shoulder rehabilitation platform',
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body className="font-sans min-h-screen">{children}</body>
    </html>
  );
}
