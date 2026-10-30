// DDS Conversa — PC Agent 설치 프로그램 (WinForms, .NET Framework 4 / C# 5)
// 빌드: scripts\build_setup_exe.ps1  (Windows 기본 csc.exe 사용, 추가 설치 불필요)
// 서버의 /api/agent/setup.exe 가 이 exe 끝에 "DDSCFG{...}" 형태로 서버 주소를 붙여서 내려준다.
// 실제 설치는 서버의 install.ps1 을 받아 실행한다(포터블 Python/Node, Agent, CLI, 자동 실행 등록).
// 화면은 웹앱 로그인 화면과 같은 구성이다: 위 로고 줄, 왼쪽 무대, 오른쪽 입력, 아래 단계 띠.
// 색은 frontend/src/design/tokens.css의 밝은 테마 값이고 test_installer.py가 어긋나지 않게 검사한다.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyTitle("DDS Agent Setup")]
[assembly: System.Reflection.AssemblyProduct("DDS Conversa")]
[assembly: System.Reflection.AssemblyVersion("1.0.0.0")]

namespace DdsAgentSetup
{
    /// 색·글꼴·크기. 좌표는 모두 96dpi 기준 값을 Px()로 바꿔 쓴다(고해상도 화면에서 흐려지지 않게).
    static class T
    {
        public static readonly Color Paper = ColorTranslator.FromHtml("#f4f3ee");      // --paper
        public static readonly Color Panel = ColorTranslator.FromHtml("#fbfaf6");      // --panel
        public static readonly Color Panel3 = ColorTranslator.FromHtml("#e7e5dd");     // --panel-3
        public static readonly Color Line = ColorTranslator.FromHtml("#d8d6ce");       // --line
        public static readonly Color Line2 = ColorTranslator.FromHtml("#e6e4dc");      // --line-2
        public static readonly Color LineStrong = ColorTranslator.FromHtml("#b9b6ac"); // --line-strong
        public static readonly Color Ink = ColorTranslator.FromHtml("#1c1c1a");        // --ink
        public static readonly Color Ink2 = ColorTranslator.FromHtml("#45443f");       // --ink-2
        public static readonly Color Ink3 = ColorTranslator.FromHtml("#6a6860");       // --ink-3
        public static readonly Color Ink4 = ColorTranslator.FromHtml("#9a988e");       // --ink-4
        public static readonly Color Hover = ColorTranslator.FromHtml("#e9e7e0");      // --hover
        public static readonly Color Track = ColorTranslator.FromHtml("#e1dfd6");      // --track
        public static readonly Color Brand = ColorTranslator.FromHtml("#d63f10");      // --brand-solid
        public static readonly Color BrandHover = ColorTranslator.FromHtml("#bc350b"); // --brand-solid-hover
        public static readonly Color BrandRing = ColorTranslator.FromHtml("#f7d9cc");  // --brand-100
        public static readonly Color OnBrand = ColorTranslator.FromHtml("#ffffff");    // --on-brand
        public static readonly Color LogoText = ColorTranslator.FromHtml("#1428a0");   // --logo-text
        public static readonly Color Mean = ColorTranslator.FromHtml("#0d9488");       // --mean
        public static readonly Color MeanSoft = ColorTranslator.FromHtml("#f0fdfa");   // --mean-soft
        public static readonly Color OkInk = ColorTranslator.FromHtml("#1f6a3d");      // --ok-ink
        public static readonly Color OkSoft = ColorTranslator.FromHtml("#e8f3ec");     // --ok-soft
        public static readonly Color OkRing = ColorTranslator.FromHtml("#bfddc9");     // --ok-ring
        public static readonly Color WarnInk = ColorTranslator.FromHtml("#7a4f00");    // --warn-ink
        public static readonly Color WarnSoft = ColorTranslator.FromHtml("#fbf3dc");   // --warn-soft
        public static readonly Color WarnRing = ColorTranslator.FromHtml("#efd892");   // --warn-ring
        public static readonly Color ErrInk = ColorTranslator.FromHtml("#9b0f28");     // --err-ink
        public static readonly Color ErrSoft = ColorTranslator.FromHtml("#fbecee");    // --err-soft
        public static readonly Color ErrRing = ColorTranslator.FromHtml("#f3c7cf");    // --err-ring

        public static float K = 1f;
        public static int Px(float n) { return (int)Math.Round(n * K); }
        public static Font Sans(float px, FontStyle style = FontStyle.Regular) { return new Font("Malgun Gothic", px * K, style, GraphicsUnit.Pixel); }
        public static Font Mono(float px, FontStyle style = FontStyle.Regular) { return new Font("Consolas", px * K, style, GraphicsUnit.Pixel); }

        public static GraphicsPath Round(Rectangle r, int radius)
        {
            var p = new GraphicsPath();
            int d = radius * 2;
            p.AddArc(r.X, r.Y, d, d, 180, 90);
            p.AddArc(r.Right - d, r.Y, d, d, 270, 90);
            p.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
            p.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
            p.CloseFigure();
            return p;
        }

        public static void Box(Graphics g, Rectangle r, int radius, Color fill, Color line)
        {
            g.SmoothingMode = SmoothingMode.AntiAlias;
            using (var p = Round(r, radius))
            using (var b = new SolidBrush(fill))
            using (var pen = new Pen(line))
            {
                g.FillPath(b, p);
                g.DrawPath(pen, p);
            }
            g.SmoothingMode = SmoothingMode.None;
        }
    }

    /// 웹앱의 주 버튼·글자 버튼과 같은 모양
    class Btn : Button
    {
        public bool Primary;
        bool hot, down;

        public Btn(string text, bool primary)
        {
            Text = text;
            Primary = primary;
            Font = T.Sans(primary ? 14 : 13, primary ? FontStyle.Bold : FontStyle.Regular);
            Cursor = Cursors.Hand;
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer, true);
        }

        protected override void OnMouseEnter(EventArgs e) { hot = true; Invalidate(); base.OnMouseEnter(e); }
        protected override void OnMouseLeave(EventArgs e) { hot = down = false; Invalidate(); base.OnMouseLeave(e); }
        protected override void OnMouseDown(MouseEventArgs e) { down = true; Invalidate(); base.OnMouseDown(e); }
        protected override void OnMouseUp(MouseEventArgs e) { down = false; Invalidate(); base.OnMouseUp(e); }
        protected override void OnGotFocus(EventArgs e) { Invalidate(); base.OnGotFocus(e); }
        protected override void OnLostFocus(EventArgs e) { Invalidate(); base.OnLostFocus(e); }

        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.Clear(Parent != null ? Parent.BackColor : T.Panel);
            var r = new Rectangle(0, 0, Width - 1, Height - 1);
            Color fill, line, ink;
            if (!Enabled) { fill = Primary ? T.Panel3 : T.Panel; line = T.Line; ink = T.Ink4; }
            else if (Primary) { fill = (hot || down) ? T.BrandHover : T.Brand; line = fill; ink = T.OnBrand; }
            else { fill = down ? T.Panel3 : hot ? T.Hover : T.Panel; line = T.LineStrong; ink = T.Ink2; }
            T.Box(e.Graphics, r, T.Px(3), fill, line);
            if (Focused && Enabled)
                using (var pen = new Pen(Primary ? T.Ink : T.Brand)) { e.Graphics.SmoothingMode = SmoothingMode.AntiAlias; using (var p = T.Round(Rectangle.Inflate(r, -2, -2), T.Px(2))) e.Graphics.DrawPath(pen, p); e.Graphics.SmoothingMode = SmoothingMode.None; }
            TextRenderer.DrawText(e.Graphics, Text, Font, r, ink, TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.NoPrefix | TextFormatFlags.SingleLine);
        }
    }

    /// 입력칸: 테두리와 포커스 고리를 웹앱과 같게 그린다
    class Field : Panel
    {
        public readonly TextBox Box = new TextBox { BorderStyle = BorderStyle.None };

        public Field(Font font, bool mono = false)
        {
            SetStyle(ControlStyles.OptimizedDoubleBuffer | ControlStyles.AllPaintingInWmPaint | ControlStyles.ResizeRedraw, true);
            Box.Font = font;
            Box.BackColor = T.Panel;
            Box.ForeColor = T.Ink;
            Controls.Add(Box);
            Box.GotFocus += delegate { Invalidate(); };
            Box.LostFocus += delegate { Invalidate(); };
            Click += delegate { Box.Focus(); };
        }

        protected override void OnLayout(LayoutEventArgs e)
        {
            base.OnLayout(e);
            int pad = T.Px(10);
            Box.SetBounds(pad, (Height - Box.PreferredHeight) / 2, Width - pad * 2, Box.PreferredHeight);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.Clear(Parent != null ? Parent.BackColor : T.Panel);
            var r = new Rectangle(1, 1, Width - 3, Height - 3);
            bool on = Box.Focused;
            if (on) T.Box(e.Graphics, new Rectangle(0, 0, Width - 1, Height - 1), T.Px(4), T.Panel, T.BrandRing);
            T.Box(e.Graphics, r, T.Px(3), T.Panel, Enabled ? (on ? T.Brand : T.LineStrong) : T.Line);
        }
    }

    /// 체크박스: 주홍으로 채운 네모. 키보드·낭독은 CheckBox 그대로 쓴다
    class Check : CheckBox
    {
        public Check()
        {
            Size = new Size(T.Px(18), T.Px(18));
            Cursor = Cursors.Hand;
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer, true);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.Clear(Parent != null ? Parent.BackColor : T.Panel);
            var r = new Rectangle(0, 0, Width - 1, Height - 1);
            Color fill = !Enabled ? T.Panel3 : Checked ? T.Brand : T.Panel;
            T.Box(e.Graphics, r, T.Px(2), fill, !Enabled ? T.Line : Checked ? T.Brand : (Focused ? T.Brand : T.LineStrong));
            if (Checked)
                using (var pen = new Pen(Enabled ? T.OnBrand : T.Ink4, T.K * 2f))
                {
                    e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
                    e.Graphics.DrawLines(pen, new[] { new PointF(Width * .24f, Height * .52f), new PointF(Width * .43f, Height * .70f), new PointF(Width * .76f, Height * .32f) });
                    e.Graphics.SmoothingMode = SmoothingMode.None;
                }
        }
    }

    /// 상태 태그(설치됨·로그인 정보 있음 …)
    class Chip : Control
    {
        Color soft, ring, ink;

        public Chip()
        {
            Font = T.Sans(12);
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer, true);
        }

        public void Set(string text, Color softColor, Color ringColor, Color inkColor)
        {
            Text = text; soft = softColor; ring = ringColor; ink = inkColor;
            Width = TextRenderer.MeasureText(text, Font, Size.Empty, TextFormatFlags.NoPadding).Width + T.Px(18);
            Height = T.Px(24);
            Visible = text.Length > 0;
            Invalidate();
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.Clear(Parent != null ? Parent.BackColor : T.Panel);
            var r = new Rectangle(0, 0, Width - 1, Height - 1);
            T.Box(e.Graphics, r, r.Height / 2, soft, ring);
            TextRenderer.DrawText(e.Graphics, Text, Font, r, ink, TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.NoPrefix);
        }
    }

    /// 진행 중임을 보이는 얇은 막대. 움직임을 끈 PC에서는 멈춰 있다
    class Bar : Control
    {
        readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer { Interval = 33 };
        float at;
        public Color Tint = T.Brand;
        public bool Running { get { return timer.Enabled; } set { timer.Enabled = value && SystemInformation.UIEffectsEnabled; Invalidate(); } }

        public Bar()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer, true);
            timer.Tick += delegate { at = (at + .012f) % 1.3f; Invalidate(); };
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.Clear(T.Track);
            using (var b = new SolidBrush(Tint))
            {
                if (timer.Enabled) e.Graphics.FillRectangle(b, (at - .3f) * Width, 0, Width * .3f, Height);
                else e.Graphics.FillRectangle(b, 0, 0, Width, Height);
            }
        }
    }

    /// 아래 단계 띠: 웹앱 로그인 화면 아래의 띠와 같은 모양
    class StepBand : Control
    {
        public int Current;
        public string[][] Steps;
        readonly Font num = T.Mono(12), head = T.Sans(14, FontStyle.Bold), sub = T.Sans(12);

        public StepBand() { SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true); }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.Clear(T.Paper);
            int w = Width / Steps.Length;
            for (int i = 0; i < Steps.Length; i++)
            {
                int x = i * w;
                if (i == Current)
                {
                    using (var soft = new SolidBrush(T.MeanSoft)) g.FillRectangle(soft, x, 0, w, Height);
                    using (var top = new SolidBrush(T.Mean)) g.FillRectangle(top, x, 0, w, T.Px(2));
                }
                using (var pen = new Pen(T.Line))
                {
                    if (i > 0) g.DrawLine(pen, x, 0, x, Height);
                    if (i != Current) g.DrawLine(pen, x, 0, x + w, 0);
                }
                bool done = i < Current;
                TextRenderer.DrawText(g, done ? "✓" : "0" + (i + 1), num, new Point(x + T.Px(24), T.Px(17)), done ? T.OkInk : T.Ink3, TextFormatFlags.NoPadding);
                TextRenderer.DrawText(g, Steps[i][0], head, new Point(x + T.Px(46), T.Px(14)), i > Current ? T.Ink3 : T.Ink, TextFormatFlags.NoPadding);
                TextRenderer.DrawText(g, Steps[i][1], sub, new Point(x + T.Px(46), T.Px(37)), T.Ink3, TextFormatFlags.NoPadding);
            }
        }
    }

    /// 왼쪽 무대: 큰 제목과, 설치하면 무엇이 어떻게 이어지는지 보여 주는 선 그림
    class Stage : Panel
    {
        readonly Font small = T.Sans(13), mono = T.Mono(11);

        public Stage() { SetStyle(ControlStyles.OptimizedDoubleBuffer | ControlStyles.AllPaintingInWmPaint | ControlStyles.ResizeRedraw, true); }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.Clear(T.Paper);
            int y = T.Px(278), h = T.Px(68), w = T.Px(136), gap = T.Px(30), x = T.Px(32);
            string[][] nodes = { new[] { "SERVER", "웹앱" }, new[] { "THIS PC", "PC Agent" }, new[] { "THIS PC", "AI CLI · CAD/CAE" } };
            using (var pen = new Pen(T.LineStrong))
                for (int i = 1; i < nodes.Length; i++)
                {
                    int lx = x + (w + gap) * i - gap, ly = y + h / 2;
                    g.DrawLine(pen, lx, ly, lx + gap, ly);
                }
            for (int i = 0; i < nodes.Length; i++)
            {
                var r = new Rectangle(x + (w + gap) * i, y, w, h);
                bool me = i == 1;
                T.Box(g, r, T.Px(3), T.Panel, me ? T.Brand : T.LineStrong);
                TextRenderer.DrawText(g, nodes[i][0], mono, new Point(r.X + T.Px(12), r.Y + T.Px(14)), me ? T.Brand : T.Ink3, TextFormatFlags.NoPadding);
                TextRenderer.DrawText(g, nodes[i][1], small, new Point(r.X + T.Px(12), r.Y + T.Px(36)), T.Ink, TextFormatFlags.NoPadding);
            }
        }
    }

    /// 지원하는 AI 에이전트 CLI. Bins는 PC Agent가 실행하는 명령까지 포함한다(agent/dds_agent/config.py와 같아야 한다).
    class AgentCli
    {
        public string Key, Label, Desc, Cred, Guide;
        public string[] Bins;
        public bool HasCli, HasAll, Tui;
        public DateTime? CredBefore;
        public Process Login;
        public DateTime LoginStart;

        public Check Box;
        public Chip InstallChip, LoginChip;
        public Label LoginNote;
        public Btn LoginBtn;
    }

    class ApiError : Exception { public ApiError(string m) : base(m) { } }

    public class SetupForm : Form
    {
        static readonly string Profile = Environment.GetEnvironmentVariable("USERPROFILE") ?? "";
        static readonly string HomeDir = Environment.GetEnvironmentVariable("DDS_AGENT_HOME") ?? Path.Combine(Environment.GetEnvironmentVariable("LOCALAPPDATA") ?? "", "DDS-Agent");
        static readonly string ConfigFile = Environment.GetEnvironmentVariable("DDS_AGENT_CONFIG") ?? Path.Combine(Profile, ".dds-agent\\agent.toml");

        readonly List<AgentCli> agents = new List<AgentCli>
        {
            new AgentCli { Key = "gemini", Label = "Gemini CLI", Desc = "Google 계정으로 로그인합니다", Bins = new[] { "gemini" }, Tui = true,
                           Cred = ".gemini\\oauth_creds.json", Guide = "처음 화면에서 로그인 방법을 물으면 회사에서 안내한 방법(보통 Login with Google)을 고릅니다." },
            new AgentCli { Key = "claude", Label = "Claude Code", Desc = "구독 계정으로 로그인합니다", Bins = new[] { "claude", "claude-agent-acp" },
                           Cred = ".claude\\.credentials.json", Guide = "브라우저에서 승인한 뒤 코드를 붙여 넣으라고 나오면 이 창에 붙여 넣습니다." },
            new AgentCli { Key = "codex", Label = "Codex CLI", Desc = "ChatGPT 계정으로 로그인합니다", Bins = new[] { "codex", "codex-acp" },
                           Cred = ".codex\\auth.json", Guide = "브라우저에서 ChatGPT 계정으로 로그인합니다." },
        };

        Panel[] pages;
        StepBand band;
        Label serverValue, accountValue, agentValue;
        // 계정
        Field serverField, idField, tokenField;
        readonly Field[] pin = new Field[6];
        Label idLabel, pinLabel, tokenLabel, accountError;
        Btn accountBtn, tokenToggle;
        bool tokenMode;
        // 선택
        Btn installBtn;
        Label agentsNote;
        // 설치
        Label phaseLabel, installTitle, installError;
        Bar bar;
        TextBox logBox;
        Btn retryBtn, backBtn;
        Process installProc;
        DateTime installStart;
        // 로그인
        Btn webBtn, reinstallBtn;
        readonly System.Windows.Forms.Timer tick = new System.Windows.Forms.Timer { Interval = 1000 };
        int pollCount;
        volatile bool statusBusy;

        string server = "", token = "";

        public SetupForm(string embeddedServer)
        {
            server = embeddedServer;
            Text = "DDS Conversa — PC Agent 설치";
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { } // exe에 넣은 D 아이콘을 창에도
            AutoScaleMode = AutoScaleMode.None;
            Font = T.Sans(14);
            BackColor = T.Paper;
            ForeColor = T.Ink;
            ClientSize = new Size(T.Px(1040), T.Px(664));
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            DoubleBuffered = true;

            Controls.Add(BuildTopBar());
            Controls.Add(BuildStage());
            var right = new Panel { Location = new Point(T.Px(540), T.Px(56)), Size = new Size(T.Px(500), T.Px(540)), BackColor = T.Panel };
            right.Paint += delegate(object s, PaintEventArgs e) { using (var p = new Pen(T.Line)) e.Graphics.DrawLine(p, 0, 0, 0, right.Height); };
            Controls.Add(right);

            pages = new[] { BuildAccountPage(), BuildAgentsPage(), BuildInstallPage(), BuildLoginPage() };
            foreach (var p in pages)
            {
                p.Location = new Point(T.Px(32), T.Px(28));
                p.Size = new Size(T.Px(436), T.Px(490));
                p.BackColor = T.Panel;
                p.Visible = false;
                right.Controls.Add(p);
            }

            band = new StepBand
            {
                Location = new Point(0, T.Px(596)),
                Size = new Size(T.Px(1040), T.Px(68)),
                Steps = new[]
                {
                    new[] { "계정 확인", "토큰을 자동으로 발급합니다" },
                    new[] { "AI 에이전트", "이 PC에 있는지 확인합니다" },
                    new[] { "설치", "필요한 것을 모두 설치합니다" },
                    new[] { "로그인", "각 AI 에이전트에 로그인합니다" },
                }
            };
            Controls.Add(band);

            tick.Tick += delegate { OnTick(); };
            FormClosing += OnClosing;
            Load += delegate { Start(); };
            Shown += delegate { if (band.Current == 0) FocusAccount(); }; // 창이 뜨기 전의 Focus()는 효과가 없어 첫 칸으로 간다
        }

        // ---------- 공통 화면 조각 ----------
        Label L(Control parent, string text, Font font, Color color, int x, int y, int w, int h)
        {
            var l = new Label { Text = text, Font = font, ForeColor = color, Location = new Point(T.Px(x), T.Px(y)), Size = new Size(T.Px(w), T.Px(h)), BackColor = parent.BackColor, UseMnemonic = false };
            parent.Controls.Add(l);
            return l;
        }

        void Head(Panel page, string title, string sub)
        {
            L(page, title, T.Sans(20, FontStyle.Bold), T.Ink, 0, 0, 436, 32);
            L(page, sub, T.Sans(13), T.Ink3, 0, 36, 436, 40);
        }

        static void Place(Control c, int x, int y, int w, int h)
        {
            c.Location = new Point(T.Px(x), T.Px(y));
            c.Size = new Size(T.Px(w), T.Px(h));
        }

        Control BuildTopBar()
        {
            var bar = new Panel { Location = Point.Empty, Size = new Size(T.Px(1040), T.Px(56)), BackColor = T.Paper };
            Image logo = null;
            try { logo = Image.FromStream(typeof(SetupForm).Assembly.GetManifestResourceStream("dds-logo.png")); } catch { }
            var word = T.Sans(18, FontStyle.Bold);
            bar.Paint += delegate(object s, PaintEventArgs e)
            {
                var g = e.Graphics;
                if (logo != null)
                {
                    g.InterpolationMode = InterpolationMode.HighQualityBicubic;
                    g.DrawImage(logo, T.Px(30), T.Px(13), T.Px(30), T.Px(30));
                }
                int x = T.Px(68), y = T.Px(16);
                TextRenderer.DrawText(g, "DDS", word, new Point(x, y), T.LogoText, TextFormatFlags.NoPadding);
                x += TextRenderer.MeasureText(g, "DDS", word, Size.Empty, TextFormatFlags.NoPadding).Width + T.Px(5);
                TextRenderer.DrawText(g, "Conversa", word, new Point(x, y), T.Ink, TextFormatFlags.NoPadding);
                string right = "PC AGENT 설치";
                var mono = T.Mono(12);
                int w = TextRenderer.MeasureText(right, mono, Size.Empty, TextFormatFlags.NoPadding).Width;
                TextRenderer.DrawText(g, right, mono, new Point(bar.Width - w - T.Px(24), T.Px(21)), T.Ink3, TextFormatFlags.NoPadding);
                using (var p = new Pen(T.Line)) g.DrawLine(p, 0, bar.Height - 1, bar.Width, bar.Height - 1);
            };
            return bar;
        }

        Control BuildStage()
        {
            var stage = new Stage { Location = new Point(0, T.Px(56)), Size = new Size(T.Px(540), T.Px(540)), BackColor = T.Paper };
            L(stage, "내 PC를\nDDS Conversa에\n연결합니다", T.Sans(40, FontStyle.Bold), T.Ink, 28, 28, 500, 170);
            L(stage, "AI 에이전트가 내 PC의 CAD·CAE 툴을 쓸 수 있게 됩니다.\nPython·Node 등 필요한 것은 사용자 폴더에만 설치합니다.\n관리자 권한은 필요 없습니다.", T.Sans(14), T.Ink2, 32, 198, 480, 64);
            string[] names = { "서버", "계정", "PC Agent" };
            Label[] values = new Label[3];
            for (int i = 0; i < 3; i++)
            {
                L(stage, names[i], T.Sans(12), T.Ink3, 32, 382 + i * 44, 480, 18);
                values[i] = L(stage, "-", T.Mono(13, FontStyle.Bold), T.Ink, 32, 400 + i * 44, 480, 20);
            }
            serverValue = values[0]; accountValue = values[1]; agentValue = values[2];
            return stage;
        }

        // ---------- 1. 계정 확인 ----------
        Panel BuildAccountPage()
        {
            var page = new Panel { BackColor = T.Panel }; // 라벨이 만들 때 바탕색을 물려받는다
            Head(page, "계정 확인", "웹앱에 로그인할 때 쓰는 아이디와 PIN을 입력하세요.\n이 PC용 토큰을 자동으로 발급하므로 복사할 일이 없습니다.");

            L(page, "서버 주소", T.Sans(13), T.Ink2, 0, 86, 436, 20);
            serverField = new Field(T.Mono(13));
            serverField.Box.AccessibleName = "서버 주소";
            Place(serverField, 0, 108, 436, 38);
            page.Controls.Add(serverField);

            idLabel = L(page, "아이디", T.Sans(13), T.Ink2, 0, 156, 436, 20);
            idField = new Field(T.Sans(14));
            idField.Box.AccessibleName = "아이디";
            Place(idField, 0, 178, 436, 38);
            page.Controls.Add(idField);

            pinLabel = L(page, "PIN (숫자 6자리)", T.Sans(13), T.Ink2, 0, 226, 436, 20);
            for (int i = 0; i < 6; i++)
            {
                var f = new Field(T.Sans(18, FontStyle.Bold));
                Place(f, i * 74, 248, 66, 48);
                f.Box.MaxLength = 1;
                f.Box.TextAlign = HorizontalAlignment.Center;
                f.Box.UseSystemPasswordChar = true;
                f.Box.AccessibleName = "PIN " + (i + 1) + "번째 자리";
                int at = i;
                f.Box.KeyPress += delegate(object s, KeyPressEventArgs e)
                {
                    if (e.KeyChar == '\r') { e.Handled = true; accountBtn.PerformClick(); return; }
                    if (!char.IsControl(e.KeyChar) && !char.IsDigit(e.KeyChar)) e.Handled = true;
                };
                f.Box.TextChanged += delegate { if (pin[at].Box.TextLength == 1 && at < 5) pin[at + 1].Box.Focus(); };
                f.Box.KeyDown += delegate(object s, KeyEventArgs e)
                {
                    if (e.KeyCode == Keys.Back && pin[at].Box.TextLength == 0 && at > 0) { pin[at - 1].Box.Clear(); pin[at - 1].Box.Focus(); e.SuppressKeyPress = true; }
                    if (e.Control && e.KeyCode == Keys.V && Clipboard.ContainsText()) { PastePin(Clipboard.GetText()); e.SuppressKeyPress = true; }
                };
                f.Box.GotFocus += delegate { pin[at].Box.SelectAll(); };
                pin[i] = f;
                page.Controls.Add(f);
            }

            tokenLabel = L(page, "PC Agent 토큰 (웹앱 > 설정에서 발급)", T.Sans(13), T.Ink2, 0, 156, 436, 20);
            tokenField = new Field(T.Mono(13));
            Place(tokenField, 0, 178, 436, 38);
            tokenField.Box.UseSystemPasswordChar = true;
            tokenField.Box.AccessibleName = "PC Agent 토큰";
            page.Controls.Add(tokenField);

            accountBtn = new Btn("확인하고 다음으로", true);
            Place(accountBtn, 0, 312, 436, 42);
            accountBtn.Click += delegate { SubmitAccount(); };
            page.Controls.Add(accountBtn);

            accountError = L(page, "", T.Sans(13), T.ErrInk, 0, 362, 436, 60);

            tokenToggle = new Btn("이미 발급한 토큰이 있습니다", false);
            Place(tokenToggle, 0, 432, 214, 32);
            tokenToggle.Click += delegate { tokenMode = !tokenMode; ShowTokenMode(); };
            page.Controls.Add(tokenToggle);
            ShowTokenMode();
            return page;
        }

        void PastePin(string text)
        {
            string digits = Regex.Replace(text, "[^0-9]", "");
            for (int i = 0; i < 6; i++) pin[i].Box.Text = i < digits.Length ? digits.Substring(i, 1) : "";
            pin[Math.Min(5, digits.Length)].Box.Focus();
        }

        void ShowTokenMode()
        {
            idLabel.Visible = idField.Visible = pinLabel.Visible = !tokenMode;
            foreach (var f in pin) f.Visible = !tokenMode;
            tokenLabel.Visible = tokenField.Visible = tokenMode;
            tokenToggle.Text = tokenMode ? "아이디·PIN으로 발급받기" : "이미 발급한 토큰이 있습니다";
            accountError.Text = "";
        }

        void SubmitAccount()
        {
            string url = serverField.Box.Text.Trim().TrimEnd('/');
            string id = idField.Box.Text.Trim();
            var code = new StringBuilder();
            foreach (var f in pin) code.Append(f.Box.Text);
            string pasted = tokenField.Box.Text.Trim();
            if (url.Length == 0) { accountError.Text = "서버 주소를 입력하세요. 웹앱 주소와 같습니다."; serverField.Box.Focus(); return; }
            if (tokenMode && pasted.Length == 0) { accountError.Text = "토큰을 붙여 넣으세요."; tokenField.Box.Focus(); return; }
            if (!tokenMode && id.Length == 0) { accountError.Text = "아이디를 입력하세요."; idField.Box.Focus(); return; }
            if (!tokenMode && code.Length != 6) { accountError.Text = "PIN 6자리를 입력하세요."; pin[Math.Min(5, code.Length)].Box.Focus(); return; }

            accountBtn.Enabled = false;
            accountBtn.Text = "확인하는 중…";
            accountError.Text = "";
            string pinText = code.ToString();
            bool byToken = tokenMode;
            Background(delegate
            {
                try
                {
                    string tok = pasted, who;
                    if (byToken)
                    {
                        who = Who(Call("GET", url + "/api/auth/me", pasted, null));
                    }
                    else
                    {
                        string res = Call("POST", url + "/api/auth/login", null, "{\"username\":" + Json(id) + ",\"pin\":" + Json(pinText) + "}");
                        who = Who(res);
                        string jwt = JsonGet(res, "access_token");
                        string made = Call("POST", url + "/api/tokens", jwt, "{\"name\":" + Json(Environment.MachineName + " 설치 프로그램") + "}");
                        tok = JsonGet(made, "token");
                        if (string.IsNullOrEmpty(tok)) throw new ApiError("토큰을 발급하지 못했습니다 → 웹앱 > 설정에서 발급해 붙여 넣으세요.");
                    }
                    Ui(delegate { Accepted(url, tok, who); });
                }
                catch (Exception ex)
                {
                    string msg = ex is ApiError ? ex.Message : "확인하지 못했습니다: " + ex.Message;
                    Ui(delegate
                    {
                        accountError.Text = msg;
                        accountBtn.Enabled = true;
                        accountBtn.Text = "확인하고 다음으로";
                    });
                }
            });
        }

        void Accepted(string url, string tok, string who)
        {
            server = url; token = tok;
            serverValue.Text = url;
            accountValue.Text = who;
            accountBtn.Enabled = true;
            accountBtn.Text = "확인하고 다음으로";
            foreach (var f in pin) f.Box.Clear(); // PIN은 토큰을 받은 뒤 바로 지운다
            Go(1);
        }

        static string Who(string json)
        {
            string name = JsonGet(json, "display_name"), id = JsonGet(json, "username");
            return string.IsNullOrEmpty(name) ? id : name + " (" + id + ")";
        }

        // ---------- 2. AI 에이전트 선택 ----------
        Panel BuildAgentsPage()
        {
            var page = new Panel { BackColor = T.Panel };
            Head(page, "AI 에이전트 선택", "이 PC에 이미 있으면 다시 설치하지 않습니다.\n구독·사용 권한이 있는 것만 고르세요.");
            int y = 86;
            foreach (var a in agents)
            {
                var cli = a;
                var row = new Panel { Location = new Point(0, T.Px(y)), Size = new Size(T.Px(436), T.Px(68)), BackColor = T.Panel };
                row.Paint += delegate(object s, PaintEventArgs e) { using (var p = new Pen(T.Line2)) e.Graphics.DrawLine(p, 0, row.Height - 1, row.Width, row.Height - 1); };
                a.Box = new Check { Location = new Point(0, T.Px(25)), AccessibleName = a.Label };
                L(row, a.Label, T.Sans(14, FontStyle.Bold), T.Ink, 30, 13, 240, 22);
                L(row, a.Desc, T.Sans(12), T.Ink3, 30, 36, 240, 20);
                a.InstallChip = new Chip();
                a.InstallChip.Set("확인 중", T.Panel, T.Line, T.Ink3);
                a.InstallChip.Location = new Point(row.Width - a.InstallChip.Width, T.Px(22));
                row.Controls.Add(a.Box);
                row.Controls.Add(a.InstallChip);
                EventHandler toggle = delegate { if (cli.Box.Enabled) cli.Box.Checked = !cli.Box.Checked; };
                row.Click += toggle;
                foreach (Control c in row.Controls) if (c is Label) c.Click += toggle;
                page.Controls.Add(row);
                y += 68;
            }
            agentsNote = L(page, "고르지 않아도 PC Agent는 설치됩니다.\nAI 에이전트는 나중에 웹앱 설정 화면에서 설치할 수 있습니다.", T.Sans(12), T.Ink3, 0, 300, 436, 38);

            installBtn = new Btn("설치 시작", true);
            Place(installBtn, 0, 352, 436, 42);
            installBtn.Click += delegate { StartInstall(); };
            page.Controls.Add(installBtn);

            var again = new Btn("다시 확인", false);
            Place(again, 0, 432, 100, 32);
            again.Click += delegate { Detect(); };
            page.Controls.Add(again);
            return page;
        }

        /// 이 PC에서 명령을 찾을 곳. PATH와, 사용자 PATH에 없어도 npm·설치 프로그램이 쓰는 폴더.
        static List<string> SearchDirs()
        {
            var dirs = new List<string>();
            string only = Environment.GetEnvironmentVariable("DDS_SETUP_PATH"); // 테스트용: 이 폴더들에서만 찾는다
            string joined = only ?? string.Join(";", new[]
            {
                Environment.GetEnvironmentVariable("PATH"),
                Environment.GetEnvironmentVariable("PATH", EnvironmentVariableTarget.User),
                Environment.GetEnvironmentVariable("PATH", EnvironmentVariableTarget.Machine),
                Path.Combine(HomeDir, "npm-global"),
                Path.Combine(Environment.GetEnvironmentVariable("APPDATA") ?? "", "npm"),
                Path.Combine(Profile, ".local\\bin"),
            });
            foreach (string d in joined.Split(';')) if (d.Trim().Length > 0 && !dirs.Contains(d.Trim())) dirs.Add(d.Trim());
            return dirs;
        }

        static bool FindCommand(string name, List<string> dirs)
        {
            foreach (string d in dirs)
                foreach (string ext in new[] { ".cmd", ".exe", ".ps1", ".bat" })
                    try { if (File.Exists(Path.Combine(d, name + ext))) return true; } catch (ArgumentException) { } // PATH에 잘못된 글자가 있어도 넘어간다
            return false;
        }

        void Detect()
        {
            foreach (var a in agents) { a.InstallChip.Set("확인 중", T.Panel, T.Line, T.Ink3); LayoutChip(a); }
            Background(delegate
            {
                var dirs = SearchDirs();
                foreach (var a in agents)
                {
                    a.HasCli = FindCommand(a.Bins[0], dirs);
                    a.HasAll = true;
                    foreach (string b in a.Bins) a.HasAll &= FindCommand(b, dirs);
                }
                Ui(ShowDetected);
            });
        }

        void ShowDetected()
        {
            bool any = false;
            foreach (var a in agents) any |= a.HasCli;
            foreach (var a in agents)
            {
                a.Box.Enabled = !a.HasAll;
                if (a.HasAll) { a.Box.Checked = false; a.InstallChip.Set("설치됨", T.OkSoft, T.OkRing, T.OkInk); }
                else if (a.HasCli) { a.Box.Checked = true; a.InstallChip.Set("연결 구성요소 필요", T.WarnSoft, T.WarnRing, T.WarnInk); }
                else { a.Box.Checked = !any && a.Key == "gemini"; a.InstallChip.Set("미설치", T.Panel, T.Line, T.Ink3); }
                LayoutChip(a);
                a.Box.Invalidate();
            }
            installBtn.Text = File.Exists(Path.Combine(HomeDir, "current.txt")) ? "PC Agent 다시 설치" : "설치 시작";
            if (band.Current == 3) RefreshLogins();
        }

        static void LayoutChip(AgentCli a) { a.InstallChip.Left = a.InstallChip.Parent.Width - a.InstallChip.Width; }

        // ---------- 3. 설치 ----------
        Panel BuildInstallPage()
        {
            var page = new Panel { BackColor = T.Panel };
            installTitle = L(page, "설치 중", T.Sans(20, FontStyle.Bold), T.Ink, 0, 0, 436, 32);
            phaseLabel = L(page, "", T.Sans(13), T.Ink3, 0, 36, 436, 22);
            bar = new Bar();
            Place(bar, 0, 64, 436, 3);
            page.Controls.Add(bar);
            logBox = new TextBox
            {
                Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical, BorderStyle = BorderStyle.FixedSingle,
                BackColor = T.Paper, ForeColor = T.Ink2, Font = T.Mono(12),
            };
            Place(logBox, 0, 80, 436, 300);
            page.Controls.Add(logBox);
            installError = L(page, "", T.Sans(13), T.ErrInk, 0, 388, 436, 44);
            retryBtn = new Btn("다시 시도", true);
            Place(retryBtn, 0, 436, 214, 42);
            retryBtn.Click += delegate { StartInstall(); };
            retryBtn.Visible = false;
            page.Controls.Add(retryBtn);
            backBtn = new Btn("이전으로", false);
            Place(backBtn, 222, 436, 214, 42);
            backBtn.Click += delegate { Go(1); };
            backBtn.Visible = false;
            page.Controls.Add(backBtn);
            return page;
        }

        string ChosenAgents()
        {
            var list = new List<string>();
            foreach (var a in agents) if (a.Box.Enabled && a.Box.Checked) list.Add(a.Key);
            return string.Join(",", list.ToArray());
        }

        void Log(string line)
        {
            if (line == null) return;
            if (InvokeRequired) { BeginInvoke(new Action<string>(Log), line); return; }
            logBox.AppendText(line + Environment.NewLine);
            if (line.StartsWith("▶")) phaseLabel.Text = line.Substring(1).Trim();
        }

        void StartInstall()
        {
            Go(2);
            installTitle.Text = "설치 중";
            installTitle.ForeColor = T.Ink;
            phaseLabel.Text = "설치 스크립트를 받는 중입니다";
            installError.Text = "";
            retryBtn.Visible = backBtn.Visible = false;
            logBox.Clear();
            bar.Tint = T.Brand;
            bar.Running = true;
            installStart = DateTime.Now;
            string agentsChosen = ChosenAgents();
            string url = server, tok = token;
            Background(delegate { RunInstall(url, tok, agentsChosen); });
        }

        void RunInstall(string url, string tok, string agentsChosen)
        {
            int code = -1;
            try
            {
                Log("설치 스크립트를 받는 중: " + url);
                string script = Call("GET", url + "/api/agent/install.ps1", null, null);
                string path = Path.Combine(Path.GetTempPath(), "dds-agent-install.ps1");
                File.WriteAllText(path, script, new UTF8Encoding(true));

                var psi = new ProcessStartInfo("powershell.exe", "-NoProfile -ExecutionPolicy Bypass -File \"" + path + "\"");
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.RedirectStandardOutput = true;
                psi.RedirectStandardError = true;
                psi.StandardOutputEncoding = Encoding.UTF8;
                psi.StandardErrorEncoding = Encoding.UTF8;
                psi.EnvironmentVariables["DDS_SERVER_URL"] = url;
                psi.EnvironmentVariables["DDS_TOKEN"] = tok;
                psi.EnvironmentVariables["DDS_AGENTS"] = agentsChosen;
                psi.EnvironmentVariables["DDS_UTF8_OUTPUT"] = "1";
                using (var p = Process.Start(psi))
                {
                    installProc = p;
                    p.OutputDataReceived += delegate(object s, DataReceivedEventArgs e) { Log(e.Data); };
                    p.ErrorDataReceived += delegate(object s, DataReceivedEventArgs e) { Log(e.Data); };
                    p.BeginOutputReadLine();
                    p.BeginErrorReadLine();
                    p.WaitForExit();
                    code = p.ExitCode;
                }
            }
            catch (Exception ex)
            {
                Log("오류: " + ex.Message);
            }
            installProc = null;
            Ui(delegate { FinishInstall(code == 0); });
        }

        void FinishInstall(bool ok)
        {
            if (ok)
            {
                bar.Running = false;
                Detect();
                Go(3);
                return;
            }
            bar.Running = false;
            bar.Tint = T.ErrInk;
            installTitle.Text = "설치하지 못했습니다";
            installTitle.ForeColor = T.ErrInk;
            phaseLabel.Text = "";
            installError.Text = "위 기록 끝부분에 원인이 있습니다. 고친 뒤 다시 시도하세요.\n해결되지 않으면 이 기록을 복사해 관리자에게 보내 주세요.";
            retryBtn.Visible = backBtn.Visible = true;
        }

        // ---------- 4. 로그인 ----------
        Panel BuildLoginPage()
        {
            var page = new Panel { BackColor = T.Panel };
            Head(page, "AI 에이전트 로그인", "각각 처음 한 번만 로그인하면 됩니다.\n[로그인]을 누르면 안내가 적힌 검은 창이 열립니다.");
            int y = 80;
            foreach (var a in agents)
            {
                var cli = a;
                var row = new Panel { Location = new Point(0, T.Px(y)), Size = new Size(T.Px(436), T.Px(70)), BackColor = T.Panel };
                row.Paint += delegate(object s, PaintEventArgs e) { using (var p = new Pen(T.Line2)) e.Graphics.DrawLine(p, 0, row.Height - 1, row.Width, row.Height - 1); };
                L(row, a.Label, T.Sans(14, FontStyle.Bold), T.Ink, 0, 11, 100, 22);
                a.LoginChip = new Chip { Location = new Point(T.Px(104), T.Px(9)) };
                a.LoginNote = L(row, "", T.Sans(12), T.Ink3, 0, 36, 340, 32);
                a.LoginBtn = new Btn("로그인", false) { Location = new Point(T.Px(350), T.Px(18)), Size = new Size(T.Px(86), T.Px(34)) };
                a.LoginBtn.AccessibleName = a.Label + " 로그인";
                a.LoginBtn.Click += delegate { OpenLogin(cli); };
                row.Controls.Add(a.LoginChip);
                row.Controls.Add(a.LoginBtn);
                page.Controls.Add(row);
                y += 70;
            }
            var notice = new Panel { Location = new Point(0, T.Px(y + 12)), Size = new Size(T.Px(436), T.Px(92)), BackColor = T.Panel };
            var noticeFont = T.Sans(13);
            notice.Paint += delegate(object s, PaintEventArgs e)
            {
                T.Box(e.Graphics, new Rectangle(0, 0, notice.Width - 1, notice.Height - 1), T.Px(3), T.WarnSoft, T.WarnRing);
                TextRenderer.DrawText(e.Graphics, "로그인은 오래 걸릴 수 있습니다. 사내 보안 환경에서는 로그인 화면이 뜰 때까지, 또 로그인이 처리될 때까지 각각 몇 분씩 걸리기도 합니다. 검은 창이 멈춘 것처럼 보여도 닫지 말고 기다려 주세요. 끝나면 여기에 표시됩니다.",
                    noticeFont, Rectangle.Inflate(new Rectangle(0, 0, notice.Width, notice.Height), -T.Px(12), -T.Px(8)), T.WarnInk, TextFormatFlags.WordBreak | TextFormatFlags.NoPrefix);
            };
            page.Controls.Add(notice);

            webBtn = new Btn("웹앱 열기", true);
            Place(webBtn, 0, 410, 300, 42);
            webBtn.Click += delegate { try { Process.Start(server); } catch { } };
            page.Controls.Add(webBtn);
            reinstallBtn = new Btn("다시 설치", false);
            Place(reinstallBtn, 308, 410, 128, 42);
            reinstallBtn.Click += delegate { Detect(); Go(1); };
            page.Controls.Add(reinstallBtn);
            return page;
        }

        static string CredPath(AgentCli a) { return Path.Combine(Profile, a.Cred); }

        static DateTime? CredTime(AgentCli a)
        {
            try { string p = CredPath(a); return File.Exists(p) ? (DateTime?)File.GetLastWriteTimeUtc(p) : null; } catch { return null; }
        }

        void RefreshLogins()
        {
            foreach (var a in agents)
            {
                bool running = a.Login != null && !a.Login.HasExited;
                DateTime? now = CredTime(a);
                bool changed = a.Login != null && now.HasValue && now != a.CredBefore;
                if (running && changed)
                {
                    SetLogin(a, "로그인 완료", T.OkSoft, T.OkRing, T.OkInk, "로그인되었습니다. 검은 창은 닫아도 됩니다.", false, "진행 중");
                }
                else if (running)
                {
                    var e = DateTime.Now - a.LoginStart;
                    SetLogin(a, "진행 중", T.WarnSoft, T.WarnRing, T.WarnInk, string.Format("검은 창에서 진행 중입니다({0}분 {1:00}초). 창을 닫지 마세요.", (int)e.TotalMinutes, e.Seconds), false, "진행 중");
                }
                else if (!a.HasCli)
                {
                    SetLogin(a, "미설치", T.Panel, T.Line, T.Ink3, "먼저 설치해야 합니다. [다시 설치]에서 고르세요.", false, "로그인");
                }
                else if (now.HasValue)
                {
                    SetLogin(a, "로그인 정보 있음", T.OkSoft, T.OkRing, T.OkInk, "이 PC에 로그인 정보가 있습니다. 안 되면 다시 로그인하세요.", true, "다시 로그인");
                }
                else
                {
                    SetLogin(a, "로그인 필요", T.Panel, T.LineStrong, T.Ink2, a.Desc, true, "로그인");
                }
            }
        }

        static void SetLogin(AgentCli a, string chip, Color soft, Color ring, Color ink, string note, bool enabled, string button)
        {
            if (a.LoginChip.Text != chip) a.LoginChip.Set(chip, soft, ring, ink);
            if (a.LoginNote.Text != note) a.LoginNote.Text = note;
            if (a.LoginBtn.Enabled != enabled) a.LoginBtn.Enabled = enabled;
            if (a.LoginBtn.Text != button) a.LoginBtn.Text = button;
            bool primary = enabled && button == "로그인";
            if (a.LoginBtn.Primary != primary) { a.LoginBtn.Primary = primary; a.LoginBtn.Invalidate(); }
        }

        void OpenLogin(AgentCli a)
        {
            try
            {
                string dir = Path.Combine(HomeDir, "login");
                Directory.CreateDirectory(dir);
                string path = Path.Combine(dir, "login-" + a.Key + ".ps1");
                File.WriteAllText(path, LoginScript(a), new UTF8Encoding(true)); // BOM이 없으면 PowerShell 5.1이 한글을 깨뜨린다

                var psi = new ProcessStartInfo("powershell.exe", "-NoProfile -ExecutionPolicy Bypass -File \"" + path + "\"");
                psi.UseShellExecute = false;
                string npmGlobal = Path.Combine(HomeDir, "npm-global");
                psi.EnvironmentVariables["PATH"] = Path.Combine(HomeDir, "runtime\\node") + ";" + npmGlobal + ";" + string.Join(";", SearchDirs().ToArray());
                psi.EnvironmentVariables["NPM_CONFIG_PREFIX"] = npmGlobal;
                a.CredBefore = CredTime(a);
                a.LoginStart = DateTime.Now;
                a.Login = Process.Start(psi);
                RefreshLogins();
            }
            catch (Exception ex)
            {
                a.LoginNote.Text = "창을 열지 못했습니다: " + ex.Message;
            }
        }

        /// 로그인 창의 스크립트. 로그인 프로그램이 아무것도 출력하지 않는 긴 대기 동안 멈춘 것으로 오해하지 않게,
        /// 제목 줄에 지난 시간을 계속 보이고, 창이 조용하면 본문에도 안내를 찍는다.
        /// 화면을 통째로 그리는 프로그램(Gemini)은 뭔가 출력하기 시작하면 본문에는 찍지 않는다(화면이 깨진다).
        static string LoginScript(AgentCli a)
        {
            string command;
            if (a.Key == "claude")
                command = @"
$help = (& claude auth --help 2>&1 | Out-String)
if ($help -match '(?m)^\s*login\b') { & claude auth login }
else {
    $state.tui = $true
    Write-Host ' claude가 열리면 /login 을 입력하고 Enter를 누르세요.' -ForegroundColor Cyan
    & claude
}";
            else if (a.Key == "codex") command = "& codex login";
            else command = "& " + a.Bins[0];

            return @"$ErrorActionPreference = 'Continue'
$name = '__NAME__'
$cred = '__CRED__'
$before = if (Test-Path $cred) { (Get-Item $cred).LastWriteTimeUtc } else { $null }
$state = [hashtable]::Synchronized(@{ tui = __TUI__ })
$Host.UI.RawUI.WindowTitle = ""$name 로그인 진행 중 · 창을 닫지 마세요""

Write-Host ''
Write-Host ' ============================================================' -ForegroundColor DarkYellow
Write-Host ""  $name 로그인"" -ForegroundColor White
Write-Host ' ============================================================' -ForegroundColor DarkYellow
Write-Host ''
Write-Host '  이 창을 닫지 마세요. 멈춘 것이 아닙니다.' -ForegroundColor Yellow
Write-Host ''
Write-Host '  사내 보안 환경에서는 시간이 걸립니다.'
Write-Host '   - 프로그램이 시작되고 로그인 화면이 뜰 때까지 몇 분'
Write-Host '   - 로그인한 뒤 실제로 처리될 때까지 또 몇 분'
Write-Host '  그동안 화면에 아무 변화가 없어도 정상입니다. 10분까지는 그대로 기다려 주세요.'
Write-Host ''
Write-Host '  순서'
Write-Host '   1. 기다리면 브라우저가 열리거나, 이 창에 로그인 주소가 나옵니다.'
Write-Host '      주소가 나오면 Ctrl을 누른 채 클릭하거나 복사해 브라우저에 붙여 넣습니다.'
Write-Host '   2. __GUIDE__'
Write-Host '   3. 끝나면 이 창 제목과 설치 창에 로그인 완료가 표시됩니다. 그때 이 창을 닫습니다.'
Write-Host ''
Write-Host '  10분이 지나도 변화가 없으면 이 창을 닫고 설치 창에서 다시 눌러 보세요.' -ForegroundColor DarkGray
Write-Host '  그래도 안 되면 사내 네트워크(프록시·방화벽) 담당자에게 문의하세요.' -ForegroundColor DarkGray
Write-Host ' ------------------------------------------------------------' -ForegroundColor DarkYellow
Write-Host ''

$beat = [powershell]::Create()
[void]$beat.AddScript({
    param($name, $cred, $before, $state)
    $start = Get-Date
    $top = [Console]::CursorTop; $left = [Console]::CursorLeft
    $quiet = 0; $printed = $false; $done = $false; $silent = $false
    while ($true) {
        Start-Sleep -Seconds 1
        try {
            $e = (Get-Date) - $start
            $t = '{0}분 {1:00}초' -f [int][math]::Floor($e.TotalMinutes), $e.Seconds
            $ok = (Test-Path $cred) -and (($null -eq $before) -or ((Get-Item $cred).LastWriteTimeUtc -ne $before))
            if ($ok) {
                [Console]::Title = ""$name 로그인 완료 · 이 창을 닫아도 됩니다""
                if (-not $done -and -not $state.tui) { [Console]::WriteLine(""  로그인 완료. 이 창을 닫아도 됩니다.""); $done = $true }
                continue
            }
            [Console]::Title = ""$name 로그인 진행 중 · $t 지남 · 창을 닫지 마세요""
            if ([Console]::CursorTop -ne $top -or [Console]::CursorLeft -ne $left) {
                $top = [Console]::CursorTop; $left = [Console]::CursorLeft; $quiet = 0
                if ($state.tui) { $silent = $true }   # 화면을 직접 그리는 프로그램과 겹치지 않게 그만 찍는다
                continue
            }
            $quiet++
            if (-not $silent -and $quiet -ge 15) {
                [Console]::WriteLine(""  ... $t 지남. 정상적으로 기다리는 중입니다. 창을 닫지 마세요."")
                $top = [Console]::CursorTop; $left = [Console]::CursorLeft; $quiet = 0
            }
        } catch { }
    }
}).AddArgument($name).AddArgument($cred).AddArgument($before).AddArgument($state)
[void]$beat.BeginInvoke()

__COMMAND__

[void]$beat.BeginStop($null, $null)
Write-Host ''
$ok = (Test-Path $cred) -and (($null -eq $before) -or ((Get-Item $cred).LastWriteTimeUtc -ne $before))
if ($ok) {
    $Host.UI.RawUI.WindowTitle = ""$name 로그인 완료""
    Write-Host '  로그인 완료. 이 창을 닫아도 됩니다.' -ForegroundColor Green
} elseif (Test-Path $cred) {
    Write-Host '  이전 로그인 정보를 그대로 씁니다. 다시 로그인했다면 창을 닫아도 됩니다.' -ForegroundColor Yellow
} else {
    Write-Host '  로그인 정보를 찾지 못했습니다. 로그인을 마치지 못했다면 설치 창에서 다시 눌러 주세요.' -ForegroundColor Yellow
}
Write-Host ''
[void](Read-Host '  닫으려면 Enter를 누르세요')
"
                .Replace("__NAME__", a.Label)
                .Replace("__CRED__", CredPath(a).Replace("'", "''"))
                .Replace("__TUI__", a.Tui ? "$true" : "$false")
                .Replace("__GUIDE__", a.Guide)
                .Replace("__COMMAND__", command);
        }

        // ---------- 화면 전환 ----------
        void Go(int step)
        {
            for (int i = 0; i < pages.Length; i++) pages[i].Visible = i == step;
            band.Current = step;
            band.Invalidate();
            if (step == 0)
            {
                serverField.Box.Text = server;
                FocusAccount();
                AcceptButton = accountBtn;
            }
            else AcceptButton = null;
            if (step == 3) RefreshLogins();
            tick.Enabled = step == 2 || step == 3;
        }

        void FocusAccount()
        {
            if (server.Length == 0) serverField.Box.Focus();
            else if (tokenMode) tokenField.Box.Focus();
            else idField.Box.Focus();
        }

        void OnTick()
        {
            if (band.Current == 2 && bar.Running)
            {
                var e = DateTime.Now - installStart;
                installTitle.Text = string.Format("설치 중 · {0}분 {1:00}초", (int)e.TotalMinutes, e.Seconds);
            }
            if (band.Current == 3)
            {
                RefreshLogins();
                if (pollCount++ % 4 == 0) CheckAgentOnline();
            }
        }

        void CheckAgentOnline()
        {
            if (statusBusy || token.Length == 0) return;
            statusBusy = true;
            string url = server, tok = token;
            Background(delegate
            {
                string text;
                try { text = Regex.IsMatch(Call("GET", url + "/api/agent/status", tok, null), "\"connected\"\\s*:\\s*true") ? "연결됨" : "연결을 기다리는 중"; }
                catch { text = "확인하지 못함"; }
                statusBusy = false;
                Ui(delegate { agentValue.Text = text; });
            });
        }

        void OnClosing(object sender, FormClosingEventArgs e)
        {
            var p = installProc;
            if (p == null) return;
            if (MessageBox.Show("설치가 진행 중입니다. 지금 닫으면 설치가 중단됩니다. 닫을까요?", Text, MessageBoxButtons.YesNo, MessageBoxIcon.Warning) != DialogResult.Yes) { e.Cancel = true; return; }
            try { p.Kill(); } catch { }
        }

        /// 시작: 이 PC가 이미 연결돼 있으면 계정을 다시 묻지 않고 로그인 단계로 간다(다시 실행해 로그인만 할 때)
        void Start()
        {
            serverValue.Text = server.Length > 0 ? server : "-";
            string embedded = server;
            Go(0);
            Detect();
            string cfgServer = "", cfgToken = "";
            try
            {
                string toml = File.Exists(ConfigFile) ? File.ReadAllText(ConfigFile) : "";
                cfgServer = Regex.Match(toml, "(?m)^\\s*server_url\\s*=\\s*'([^']*)'").Groups[1].Value.TrimEnd('/');
                cfgToken = Regex.Match(toml, "(?m)^\\s*token\\s*=\\s*'([^']*)'").Groups[1].Value;
            }
            catch { }
            if (cfgToken.Length == 0) return;
            if (embedded.Length > 0 && !string.Equals(embedded.TrimEnd('/'), cfgServer, StringComparison.OrdinalIgnoreCase)) return; // 다른 서버의 설치 프로그램이다
            Background(delegate
            {
                try
                {
                    string who = Who(Call("GET", cfgServer + "/api/auth/me", cfgToken, null));
                    Ui(delegate
                    {
                        if (band.Current != 0 || !accountBtn.Enabled) return;
                        Accepted(cfgServer, cfgToken, who);
                        if (File.Exists(Path.Combine(HomeDir, "current.txt"))) Go(3);
                    });
                }
                catch { } // 토큰이 폐기됐거나 서버에 닿지 않으면 계정 단계부터 한다
            });
        }

        // ---------- 서버 호출 ----------
        static string Call(string method, string url, string bearer, string body)
        {
            var req = (HttpWebRequest)WebRequest.Create(url);
            req.Method = method;
            req.Timeout = 30000;
            req.ReadWriteTimeout = 30000;
            if (bearer != null) req.Headers["Authorization"] = "Bearer " + bearer;
            try
            {
                if (body != null)
                {
                    byte[] data = Encoding.UTF8.GetBytes(body);
                    req.ContentType = "application/json";
                    req.ContentLength = data.Length;
                    using (var s = req.GetRequestStream()) s.Write(data, 0, data.Length);
                }
                using (var res = req.GetResponse())
                using (var r = new StreamReader(res.GetResponseStream(), Encoding.UTF8))
                    return r.ReadToEnd();
            }
            catch (WebException ex)
            {
                var res = ex.Response as HttpWebResponse;
                if (res == null)
                    throw new ApiError("서버에 연결할 수 없습니다(" + url.Split('/')[2] + ") → 서버 주소가 웹앱 주소와 같은지, 사내망에 연결되어 있는지 확인하세요.");
                string text = "";
                using (var r = new StreamReader(res.GetResponseStream(), Encoding.UTF8)) text = r.ReadToEnd();
                string detail = JsonGet(text, "detail");
                if ((int)res.StatusCode == 401 && bearer != null) throw new ApiError("토큰이 올바르지 않습니다 → 아이디·PIN으로 새로 발급받으세요.");
                if (!string.IsNullOrEmpty(detail)) throw new ApiError(detail);
                throw new ApiError("서버 오류(" + (int)res.StatusCode + ") → 잠시 뒤 다시 시도하세요. 계속되면 관리자에게 문의하세요.");
            }
        }

        static string Json(string s)
        {
            var b = new StringBuilder("\"");
            foreach (char c in s)
            {
                if (c == '"' || c == '\\') b.Append('\\').Append(c);
                else if (c < ' ') b.AppendFormat("\\u{0:x4}", (int)c);
                else b.Append(c);
            }
            return b.Append('"').ToString();
        }

        static string JsonGet(string json, string key)
        {
            var m = Regex.Match(json, "\"" + key + "\"\\s*:\\s*\"((?:[^\"\\\\]|\\\\.)*)\"");
            if (!m.Success) return null;
            try { return Regex.Unescape(m.Groups[1].Value); } catch { return m.Groups[1].Value; }
        }

        void Ui(Action a)
        {
            try { if (IsHandleCreated && !IsDisposed) BeginInvoke(a); } catch (ObjectDisposedException) { } catch (InvalidOperationException) { }
        }

        static void Background(ThreadStart work)
        {
            var t = new Thread(work);
            t.IsBackground = true;
            t.Start();
        }

        static string ReadEmbeddedServer()
        {
            try
            {
                byte[] all = File.ReadAllBytes(Application.ExecutablePath);
                int n = Math.Min(4096, all.Length);
                string tail = Encoding.UTF8.GetString(all, all.Length - n, n);
                int i = tail.LastIndexOf("DDSCFG{");
                if (i < 0) return "";
                return (JsonGet(tail.Substring(i + 6), "server") ?? "").TrimEnd('/');
            }
            catch { return ""; }
        }

        [DllImport("user32.dll")]
        static extern bool SetProcessDPIAware();

        [STAThread]
        static void Main()
        {
            try { SetProcessDPIAware(); } catch { } // 고해상도 화면에서 흐려지지 않게. 크기는 T.K로 직접 맞춘다
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            using (var g = Graphics.FromHwnd(IntPtr.Zero)) T.K = g.DpiX / 96f;
            float forced;
            if (float.TryParse(Environment.GetEnvironmentVariable("DDS_SETUP_SCALE"), out forced)) T.K = forced; // 테스트용: 다른 화면 배율 흉내
            Application.Run(new SetupForm(ReadEmbeddedServer()));
        }
    }
}
