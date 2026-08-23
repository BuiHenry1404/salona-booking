import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // host: true để điện thoại cùng wifi truy cập được khi kiểm tay (task 8),
  // vite preview mặc định chỉ nghe localhost.
  server: { host: true },
  preview: { host: true },
})
