# AstroChoronoscope

## Prerequisites

- Node.js
- pnpm
- uv (Install it through `irm https://astral.sh/uv/install.ps1 | iex`)

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/greenbugx/AstroChronoscope
cd AstroChoronoscope
```

### 2. Install dependencies

```bash
pnpm install
```

### 3. Run the development server

```bash
pnpm run dev
```

### 4. Run the backend

```bash
cd backend/
uv run fastapi dev main.py
```

## Usage

1. Start the FastAPI backend (if not already running).
2. Open the application in your browser (usually `http://localhost:5173`).
3. Click "Test Backend Connection" to verify the connection.

## License

Check [LICENSE](LICENSE) for more information.