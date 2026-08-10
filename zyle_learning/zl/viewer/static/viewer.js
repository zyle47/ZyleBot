"use strict";

const canvas = document.getElementById("board");
const ctx = canvas.getContext("2d");
const el = {
  score: document.getElementById("score"),
  lives: document.getElementById("lives"),
  bricks: document.getElementById("bricks"),
  status: document.getElementById("status"),
  record: document.getElementById("record"),
  banner: document.getElementById("banner"),
};

const BALL_R = 7;
const PADDLE_H = 14;
const PADDLE_Y = 560;

let source = null;
let level = 1;
let bricks = [];      // {x,y,w,h,t,a,alive}
let paddleX = 400;
let paddleW = 110;
let balls = [];
let pierce = 0;
let played = 0;
let cleared = 0;
let generation = null;   // bumps when the trainer publishes a better checkpoint

// Brick colors: by durability (t = max hits), or by level-4 art char.
const ART = {
  Y: "#ffe23a", R: "#ff3a5e", K: "#20242e", B: "#8a5a2b",
  E: "#63ff8a", C: "#f3ead2",
};
function brickColor(b) {
  if (b.a && ART[b.a]) return ART[b.a];
  return { 1: "#38b6ff", 2: "#ffb547", 3: "#ff5ec4", 4: "#63ff8a", 5: "#d7e4ee" }[b.t] || "#38b6ff";
}

function draw() {
  ctx.clearRect(0, 0, 800, 600);

  // faint grid
  ctx.strokeStyle = "rgba(56,230,255,0.05)";
  ctx.lineWidth = 1;
  for (let x = 0; x <= 800; x += 40) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, 600); ctx.stroke(); }
  for (let y = 0; y <= 600; y += 40) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(800, y); ctx.stroke(); }

  // bricks
  for (const b of bricks) {
    if (!b.alive) continue;
    const c = brickColor(b);
    ctx.fillStyle = c;
    ctx.fillRect(b.x, b.y, b.w, b.h);
    if (b.t >= 2) {  // durability outline for multi-hit bricks
      ctx.strokeStyle = "rgba(255,255,255,0.55)";
      ctx.lineWidth = 1.5;
      ctx.strokeRect(b.x + 1, b.y + 1, b.w - 2, b.h - 2);
    }
  }

  // paddle
  ctx.fillStyle = "#6ff0ff";
  ctx.shadowColor = "rgba(56,230,255,0.9)";
  ctx.shadowBlur = 16;
  ctx.fillRect(paddleX - paddleW / 2, PADDLE_Y, paddleW, PADDLE_H);
  ctx.shadowBlur = 0;

  // balls
  for (const [x, y] of balls) {
    ctx.beginPath();
    ctx.arc(x, y, BALL_R, 0, Math.PI * 2);
    ctx.fillStyle = pierce > 0 ? "#fff2a8" : "#ffffff";
    ctx.shadowColor = pierce > 0 ? "rgba(255,220,120,0.9)" : "rgba(255,255,255,0.7)";
    ctx.shadowBlur = pierce > 0 ? 22 : 12;
    ctx.fill();
    ctx.shadowBlur = 0;
  }
}

function showBanner(text, cls) {
  el.banner.textContent = text;
  el.banner.className = "banner " + cls;
}
function hideBanner() { el.banner.className = "banner hidden"; }

function connect(newLevel) {
  level = newLevel;
  if (source) source.close();
  bricks = []; balls = [];
  el.status.textContent = "connecting…";
  el.status.className = "dim";
  hideBanner();

  source = new EventSource(`/stream?level=${level}`);

  source.addEventListener("init", (e) => {
    const d = JSON.parse(e.data);
    paddleW = d.paddleW;
    bricks = d.bricks.map((b) => ({ ...b, alive: true }));
    el.score.textContent = d.score;
    el.lives.textContent = d.lives;
    el.bricks.textContent = bricks.length;
    // A bumped generation means training just published a better checkpoint.
    if (generation !== null && d.generation !== generation) {
      played = 0; cleared = 0;
      el.record.textContent = "0 cleared / 0 played";
      el.status.textContent = "NEW BRAIN ↻";
      el.status.className = "fresh";
      setTimeout(() => { if (el.status.textContent === "NEW BRAIN ↻") { el.status.textContent = "PLAYING"; el.status.className = ""; } }, 2500);
    } else {
      el.status.textContent = "PLAYING";
      el.status.className = "";
    }
    generation = d.generation;
    hideBanner();
    draw();
  });

  source.addEventListener("frame", (e) => {
    const d = JSON.parse(e.data);
    paddleX = d.paddleX;
    balls = d.balls;
    pierce = d.pierce;
    for (const i of d.dead) if (bricks[i]) bricks[i].alive = false;
    el.score.textContent = d.score;
    el.lives.textContent = d.lives;
    el.bricks.textContent = d.aliveCount;
    draw();
  });

  source.addEventListener("result", (e) => {
    const d = JSON.parse(e.data);
    played += 1;
    if (d.cleared) {
      cleared += 1;
      showBanner("CLEARED! 🎉", "win");
    } else {
      showBanner(`GAME OVER · ${d.bricksLeft} left`, "lose");
    }
    el.record.textContent = `${cleared} cleared / ${played} played`;
    draw();
  });

  source.onerror = () => {
    el.status.textContent = "reconnecting…";
    el.status.className = "dim";
  };
}

document.getElementById("levels").addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  document.querySelectorAll(".levels button").forEach((b) => b.classList.remove("active"));
  btn.classList.add("active");
  played = 0; cleared = 0;
  generation = null;
  el.record.textContent = "0 cleared / 0 played";
  connect(Number(btn.dataset.level));
});

connect(1);
