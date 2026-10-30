// DDS Conversa — PC Agent 설치 프로그램 (WinForms, .NET Framework 4 / C# 5)
// 빌드: scripts\build_setup_exe.ps1  (Windows 기본 csc.exe 사용, 추가 설치 불필요)
// 서버의 /api/agent/setup.exe 가 이 exe 끝에 "DDSCFG{...}" 형태로 서버 주소를 붙여서 내려준다.
// 실제 설치는 서버의 install.ps1 을 받아 실행한다(포터블 Python/Node, Agent, CLI, 자동 실행 등록).
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Text;
using System.Threading;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyTitle("DDS Agent Setup")]
[assembly: System.Reflection.AssemblyProduct("DDS Conversa")]
[assembly: System.Reflection.AssemblyVersion("1.0.0.0")]

namespace DdsAgentSetup
{
    public class SetupForm : Form
    {
        static readonly Color Brand = Color.FromArgb(0x25, 0x63, 0xEB);
        static readonly Color Ink = Color.FromArgb(0x18, 0x18, 0x1B);
        static readonly Color Ink3 = Color.FromArgb(0x71, 0x71, 0x7A);
        static readonly Color Line = Color.FromArgb(0xEC, 0xEC, 0xEF);
        static readonly Color Paper = Color.FromArgb(0xF7, 0xF7, 0xF8);

        TextBox serverBox, tokenBox, logBox;
        CheckBox cbGemini, cbClaude, cbCodex;
        Button installBtn, loginBtn;
        Label statusLabel;
        string homeDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "DDS-Agent");

        public SetupForm(string server)
        {
            Text = "DDS Conversa — PC Agent 설치";
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { } // exe에 넣은 D 아이콘을 창에도
            Font = new Font("Malgun Gothic", 9.5f);
            BackColor = Paper;
            ClientSize = new Size(560, 600);
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;

            var title = new Label { Text = "PC Agent 설치", Font = new Font("Malgun Gothic", 16f, FontStyle.Bold), ForeColor = Ink,
                                    Location = new Point(24, 20), AutoSize = true };
            var sub = new Label { Text = "내 PC의 CAD/CAE 툴과 AI 에이전트를 웹앱에 연결합니다. Python·Node 등 필요한 것은 모두 자동으로 설치됩니다.",
                                  ForeColor = Ink3, Location = new Point(26, 56), Size = new Size(510, 36) };

            var card = new Panel { Location = new Point(24, 98), Size = new Size(512, 190), BackColor = Color.White };
            card.Paint += delegate(object s, PaintEventArgs e) { e.Graphics.DrawRectangle(new Pen(Line), 0, 0, card.Width - 1, card.Height - 1); };

            card.Controls.Add(Lbl("서버 주소", 16, 14));
            serverBox = new TextBox { Text = server, Location = new Point(16, 34), Width = 480 };
            card.Controls.Add(serverBox);
            card.Controls.Add(Lbl("PC Agent 토큰 (웹앱 > 설정 > PC Agent 에서 발급·복사)", 16, 66));
            tokenBox = new TextBox { Location = new Point(16, 86), Width = 480, UseSystemPasswordChar = true };
            card.Controls.Add(tokenBox);
            card.Controls.Add(Lbl("사용할 AI 에이전트 (구독·사용 권한이 있는 것)", 16, 120));
            cbGemini = new CheckBox { Text = "Gemini CLI", Location = new Point(16, 142), AutoSize = true, Checked = true };
            cbClaude = new CheckBox { Text = "Claude Code", Location = new Point(146, 142), AutoSize = true };
            cbCodex = new CheckBox { Text = "Codex CLI", Location = new Point(286, 142), AutoSize = true };
            card.Controls.AddRange(new Control[] { cbGemini, cbClaude, cbCodex });

            logBox = new TextBox { Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical, Location = new Point(24, 300),
                                   Size = new Size(512, 220), BackColor = Color.White, Font = new Font("Consolas", 9f),
                                   BorderStyle = BorderStyle.FixedSingle };
            statusLabel = new Label { Text = "", ForeColor = Ink3, Location = new Point(24, 538), Size = new Size(300, 40) };

            loginBtn = new Button { Text = "로그인 창 열기", Location = new Point(300, 536), Size = new Size(110, 38), Visible = false,
                                    FlatStyle = FlatStyle.Flat, BackColor = Color.White };
            loginBtn.FlatAppearance.BorderColor = Line;
            loginBtn.Click += delegate { OpenLoginTerminals(); };

            installBtn = new Button { Text = "설치", Location = new Point(420, 536), Size = new Size(116, 38), FlatStyle = FlatStyle.Flat,
                                      BackColor = Brand, ForeColor = Color.White, Font = new Font("Malgun Gothic", 10f, FontStyle.Bold) };
            installBtn.FlatAppearance.BorderSize = 0;
            installBtn.Click += delegate { StartInstall(); };

            Controls.AddRange(new Control[] { title, sub, card, logBox, statusLabel, loginBtn, installBtn });
            AcceptButton = installBtn;
        }

        static Label Lbl(string text, int x, int y)
        {
            return new Label { Text = text, ForeColor = Ink3, Location = new Point(x, y), AutoSize = true, Font = new Font("Malgun Gothic", 8.5f) };
        }

        string ChosenAgents()
        {
            var list = new System.Collections.Generic.List<string>();
            if (cbGemini.Checked) list.Add("gemini");
            if (cbClaude.Checked) list.Add("claude");
            if (cbCodex.Checked) list.Add("codex");
            return string.Join(",", list.ToArray());
        }

        void Log(string line)
        {
            if (line == null) return;
            if (InvokeRequired) { BeginInvoke(new Action<string>(Log), line); return; }
            logBox.AppendText(line + Environment.NewLine);
        }

        void StartInstall()
        {
            string server = serverBox.Text.Trim().TrimEnd('/');
            string token = tokenBox.Text.Trim();
            if (server.Length == 0 || token.Length == 0)
            {
                MessageBox.Show("서버 주소와 토큰을 입력하세요.", Text, MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }
            installBtn.Enabled = false;
            loginBtn.Visible = false;
            statusLabel.Text = "설치 중입니다... (수 분 걸릴 수 있습니다)";
            logBox.Clear();
            string agents = ChosenAgents();
            var t = new Thread(delegate() { RunInstall(server, token, agents); });
            t.IsBackground = true;
            t.Start();
        }

        void RunInstall(string server, string token, string agents)
        {
            int code = -1;
            try
            {
                Log("설치 스크립트를 받는 중: " + server);
                ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
                string script;
                using (var wc = new WebClient())
                {
                    wc.Encoding = Encoding.UTF8;
                    script = wc.DownloadString(server + "/api/agent/install.ps1");
                }
                string path = Path.Combine(Path.GetTempPath(), "dds-agent-install.ps1");
                File.WriteAllText(path, script, new UTF8Encoding(true));

                var psi = new ProcessStartInfo("powershell.exe",
                    "-NoProfile -ExecutionPolicy Bypass -File \"" + path + "\"");
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.RedirectStandardOutput = true;
                psi.RedirectStandardError = true;
                psi.StandardOutputEncoding = Encoding.UTF8;
                psi.StandardErrorEncoding = Encoding.UTF8;
                psi.EnvironmentVariables["DDS_SERVER_URL"] = server;
                psi.EnvironmentVariables["DDS_TOKEN"] = token;
                psi.EnvironmentVariables["DDS_AGENTS"] = agents;
                psi.EnvironmentVariables["DDS_UTF8_OUTPUT"] = "1";
                using (var p = Process.Start(psi))
                {
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
            BeginInvoke(new Action(delegate { Finish(code == 0, agents); }));
        }

        void Finish(bool ok, string agents)
        {
            installBtn.Enabled = true;
            if (ok)
            {
                installBtn.Text = "다시 설치";
                statusLabel.Text = "설치 완료. 웹앱에서 'PC Agent 연결됨'을 확인하세요.";
                statusLabel.ForeColor = Color.FromArgb(0x16, 0xA3, 0x4A);
                if (agents.Length > 0)
                {
                    loginBtn.Visible = true;
                    MessageBox.Show("설치가 끝났습니다.\n\n처음 한 번은 AI 에이전트에 로그인해야 합니다.\n[로그인 창 열기]를 눌러 각 창에서 로그인하세요.",
                                    Text, MessageBoxButtons.OK, MessageBoxIcon.Information);
                }
            }
            else
            {
                statusLabel.Text = "설치에 실패했습니다. 로그를 확인하세요.";
                statusLabel.ForeColor = Color.FromArgb(0xDC, 0x26, 0x26);
            }
        }

        void OpenLoginTerminals()
        {
            string node = Path.Combine(homeDir, "runtime\\node");
            string npmGlobal = Path.Combine(homeDir, "npm-global");
            foreach (string a in ChosenAgents().Split(','))
            {
                string cmd = a == "codex" ? "codex login" : a;
                if (a.Length == 0) continue;
                var psi = new ProcessStartInfo("powershell.exe",
                    "-NoExit -Command \"$host.UI.RawUI.WindowTitle='" + a + " 로그인'; Write-Host '로그인 후 이 창을 닫으세요.' -ForegroundColor Cyan; " + cmd + "\"");
                psi.UseShellExecute = false;
                psi.EnvironmentVariables["PATH"] = node + ";" + npmGlobal + ";" + Environment.GetEnvironmentVariable("PATH");
                psi.EnvironmentVariables["NPM_CONFIG_PREFIX"] = npmGlobal;
                Process.Start(psi);
            }
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
                string json = tail.Substring(i + 6);
                int s = json.IndexOf("\"server\":\"");
                if (s < 0) return "";
                s += 10;
                return json.Substring(s, json.IndexOf('"', s) - s);
            }
            catch { return ""; }
        }

        [STAThread]
        static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new SetupForm(ReadEmbeddedServer()));
        }
    }
}
