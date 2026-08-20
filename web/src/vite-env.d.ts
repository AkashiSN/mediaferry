/// <reference types="vite/client" />

// CSS や画像の副作用 import、`import.meta.env` の型はここから来る。
// **無いと TypeScript 7 で `import "./styles.css"` が TS2882 になる**
// （5.x は宣言の無いモジュールを黙って通していた）。
