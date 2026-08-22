/// <reference types="vite/client" />

// CSS や画像の副作用 import、`import.meta.env` の型はここから来る。
// **無いと `import "./styles.css"` が TS2882 になる**（TypeScript 7 は宣言の
// 無いモジュールを通さない）。
