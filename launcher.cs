using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

// Портативный лаунчер: запускает runtime\pythonw.exe gui.py
// из папки, где лежит сам exe. Ничего не пишет в реестр.
static class Launcher
{
    static void Main()
    {
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        string pyw = Path.Combine(dir, "runtime", "pythonw.exe");
        string gui = Path.Combine(dir, "gui.py");

        if (!File.Exists(pyw) || !File.Exists(gui))
        {
            MessageBox.Show(
                "Не найдены runtime\\pythonw.exe или gui.py рядом с лаунчером.\n" +
                "Exe должен лежать в корне папки Transcriber-XXL-Portable.",
                "Transcriber XXL", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }

        var psi = new ProcessStartInfo(pyw, "\"" + gui + "\"");
        psi.WorkingDirectory = dir;
        psi.UseShellExecute = false;
        Process.Start(psi);
    }
}
