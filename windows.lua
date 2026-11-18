-- Native Windows boundary. Requests travel only over stdin, never in executable code.
local M = {}

local SCRIPT = [=[
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false, $true)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$stdout = [Console]::OpenStandardOutput()

function Native {
    if ('UcpWindowsNative' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;
public static class UcpWindowsNative {
    const uint Backup = 0x02000000, NoFollow = 0x00200000;
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern SafeFileHandle CreateFileW(string p, uint a, uint s, IntPtr sa, uint c, uint f, IntPtr t);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern uint GetFinalPathNameByHandleW(SafeFileHandle h, StringBuilder p, uint n, uint f);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern uint GetFileAttributesW(string p);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool MoveFileExW(string s, string t, uint flags);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool CreateDirectoryW(string p, IntPtr sa);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool RemoveDirectoryW(string p);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    [return: MarshalAs(UnmanagedType.U1)]
    static extern bool CreateSymbolicLinkW(string p, string t, uint flags);
    [DllImport("kernel32.dll", SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool DeviceIoControl(SafeFileHandle h, uint code, byte[] input, uint ni,
        byte[] output, uint no, out uint returned, IntPtr overlapped);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool GetVolumePathNameW(string p, StringBuilder root, uint n);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool GetVolumeInformationW(string root, StringBuilder name, uint nn,
        out uint serial, out uint max, out uint flags, StringBuilder fs, uint nf);
    static void Error() { throw new Win32Exception(Marshal.GetLastWin32Error()); }
    static string Path(string p) {
        if (String.IsNullOrEmpty(p) || p.IndexOf('\0') >= 0) throw new ArgumentException();
        p = p.Replace('/', '\\');
        if (p.Length >= 3 && Char.IsLetter(p[0]) && p[1] == ':' && p[2] == '\\')
            return @"\\?\" + p;
        if (p.StartsWith(@"\\", StringComparison.Ordinal) && !p.StartsWith(@"\\?\", StringComparison.Ordinal)
            && !p.StartsWith(@"\\.\", StringComparison.Ordinal)) return @"\\?\UNC\" + p.Substring(2);
        throw new ArgumentException();
    }
    static SafeFileHandle Open(string p, uint access, bool noFollow) {
        SafeFileHandle h = CreateFileW(Path(p), access, 7, IntPtr.Zero, 3,
            Backup | (noFollow ? NoFollow : 0), IntPtr.Zero);
        if (h.IsInvalid) { h.Dispose(); Error(); }
        return h;
    }
    public static uint Attributes(string p) {
        uint a = GetFileAttributesW(Path(p));
        if (a == UInt32.MaxValue) Error();
        return a;
    }
    public static string Canonical(string p) {
        using (SafeFileHandle h = Open(p, 0, false)) {
            StringBuilder b = new StringBuilder(512);
            uint n = GetFinalPathNameByHandleW(h, b, (uint)b.Capacity, 0);
            if (n == 0) Error();
            if (n >= b.Capacity) {
                b = new StringBuilder(checked((int)n + 1));
                n = GetFinalPathNameByHandleW(h, b, (uint)b.Capacity, 0);
                if (n == 0) Error();
                if (n >= b.Capacity) throw new IOException();
            }
            string result = b.ToString();
            if (result.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase))
                result = @"\\" + result.Substring(8);
            else if (result.StartsWith(@"\\?\", StringComparison.Ordinal)) result = result.Substring(4);
            Path(result);
            return result.Replace('\\', '/');
        }
    }
    public static void RequireAcl(string p) {
        if ((Attributes(p) & 0x400) != 0) throw new IOException();
        StringBuilder root = new StringBuilder(32768);
        if (!GetVolumePathNameW(Path(p), root, (uint)root.Capacity)) Error();
        uint serial, max, flags;
        if (!GetVolumeInformationW(root.ToString(), null, 0, out serial, out max, out flags, null, 0)) Error();
        if ((flags & 8) == 0) throw new NotSupportedException();
    }
    public static void Move(string source, string target) {
        // No COPY_ALLOWED or REPLACE_EXISTING: rename within one volume only.
        if (!MoveFileExW(Path(source), Path(target), 0)) Error();
    }
    static string LinkText(byte[] data, int start, int offset, int length, int end) {
        if ((offset & 1) != 0 || (length & 1) != 0 || start + offset + length > end)
            throw new IOException();
        string text = Encoding.Unicode.GetString(data, start + offset, length);
        if (text.Length == 0 || text.IndexOf('\0') >= 0) throw new IOException();
        return text;
    }
    public static void CopyLink(string source, string target) {
        uint attributes = Attributes(source);
        if ((attributes & 0x400) == 0) throw new IOException();
        byte[] data = new byte[16384];
        uint count;
        using (SafeFileHandle h = Open(source, 0, true)) {
            if (!DeviceIoControl(h, 0x000900A8, null, 0, data, (uint)data.Length, out count, IntPtr.Zero)) Error();
        }
        if (count < 8) throw new IOException();
        int size = 8 + BitConverter.ToUInt16(data, 4);
        if (size > count) throw new IOException();
        uint tag = BitConverter.ToUInt32(data, 0);
        string destination = Path(target);
        if (tag == 0xA000000C) {
            if (size < 20) throw new IOException();
            uint flags = BitConverter.ToUInt32(data, 16);
            if (flags > 1) throw new NotSupportedException();
            string text = LinkText(data, 20, BitConverter.ToUInt16(data, 8), BitConverter.ToUInt16(data, 10), size);
            if (flags == 0) {
                if (text.StartsWith(@"\??\", StringComparison.Ordinal)) text = @"\\?\" + text.Substring(4);
                else throw new NotSupportedException();
            }
            uint kind = (attributes & 0x10) != 0 ? 1U : 0U;
            if (CreateSymbolicLinkW(destination, text, kind | 2)) return;
            int error = Marshal.GetLastWin32Error();
            // Older Windows lacks the developer-mode flag; never substitute a file copy.
            if (error == 87) {
                if (CreateSymbolicLinkW(destination, text, kind)) return;
                error = Marshal.GetLastWin32Error();
            }
            throw new Win32Exception(error);
        }
        if (tag != 0xA0000003 || size < 16 || (attributes & 0x10) == 0)
            throw new NotSupportedException();
        LinkText(data, 16, BitConverter.ToUInt16(data, 8), BitConverter.ToUInt16(data, 10), size);
        if (!CreateDirectoryW(destination, IntPtr.Zero)) Error();
        try {
            using (SafeFileHandle h = Open(target, 0x40000000, true)) {
                uint returned;
                if (!DeviceIoControl(h, 0x000900A4, data, (uint)size, null, 0, out returned, IntPtr.Zero)) Error();
            }
        } catch {
            // Only the empty directory created above belongs to this operation.
            if (!RemoveDirectoryW(destination)) throw new IOException("Cannot remove incomplete link.");
            throw;
        }
    }
}
'@
}

function Clipboard-Object {
    Add-Type -AssemblyName System.Windows.Forms
    return [System.Windows.Forms.Clipboard]::GetDataObject()
}

function Clipboard-Formats($data) {
    if ($null -eq $data) { return @() }
    return @($data.GetFormats($false))
}

function Recognized($formats) {
    $result = New-Object 'System.Collections.Generic.List[string]'
    if ($formats -contains 'FileDrop') { $result.Add('text/uri-list') }
    if (($formats -contains 'Bitmap') -or ($formats -contains 'DeviceIndependentBitmap') -or
        ($formats -contains 'PNG') -or ($formats -contains 'image/png')) { $result.Add('image/png') }
    if (($formats -contains 'UnicodeText') -or ($formats -contains 'Text') -or
        ($formats -contains 'OEMText')) { $result.Add('text/plain') }
    return $result.ToArray()
}

function Comparison-Path([string]$path) {
    if ([string]::IsNullOrEmpty($path) -or $path.Contains('\') -or $path -match '[\x00-\x1f]') { throw 'Invalid path' }
    if ($path -match '^[A-Za-z]:/') {
        $tail = $path.Substring(3)
    } elseif ($path -match '^//[^/]+/[^/]+(?:/|$)') {
        $root = [regex]::Match($path, '^//[^/]+/[^/]+').Value
        foreach ($part in $root.Substring(2).Split('/')) {
            if ($part -eq '.' -or $part -eq '..' -or $part -match '[:<>"|?*]') { throw 'Invalid root' }
        }
        $tail = $path.Substring($root.Length)
        if ($tail.StartsWith('/')) { $tail = $tail.Substring(1) }
    } else { throw 'Invalid root' }
    if ($tail.Length -gt 0) {
        if ($tail.EndsWith('/')) { $tail = $tail.Substring(0, $tail.Length - 1) }
        foreach ($part in $tail.Split('/')) {
            if (!$part -or $part -eq '.' -or $part -eq '..' -or $part -match '[:<>"|?*]') { throw 'Invalid component' }
        }
    }
    return $path.TrimEnd('/')
}

function Acl-Info([string]$path) {
    Native
    [UcpWindowsNative]::RequireAcl($path)
    $attributes = [System.IO.File]::GetAttributes($path)
    $directory = ($attributes -band [System.IO.FileAttributes]::Directory) -ne 0
    if ($directory) {
        $acl = [System.IO.Directory]::GetAccessControl($path, [System.Security.AccessControl.AccessControlSections]::Access)
    } else {
        $acl = [System.IO.File]::GetAccessControl($path, [System.Security.AccessControl.AccessControlSections]::Access)
    }
    return @{ attributes = $attributes; directory = $directory; acl = $acl }
}

function Set-AclInfo([string]$path, $directory, $acl) {
    if ($directory) { [System.IO.Directory]::SetAccessControl($path, $acl) }
    else { [System.IO.File]::SetAccessControl($path, $acl) }
}

function Write-Header([long]$length) {
    $script:responded = $true
    [Console]::Write("OK $length`n")
    [Console]::Out.Flush()
}

function Write-Text([string]$text) {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($text)
    Write-Header $bytes.Length
    if ($bytes.Length -gt 0) {
        $stdout.Write($bytes, 0, $bytes.Length)
        $stdout.WriteByte(10)
        $stdout.Flush()
    }
}

function Invoke-Request($envelope) {
    $request = $envelope.request
    switch ([string]$envelope.operation) {
        'copy' {
            Add-Type -AssemblyName System.Windows.Forms
            if ($null -eq $request.paths -or @($request.paths).Count -eq 0) { throw 'No files' }
            $paths = New-Object System.Collections.Specialized.StringCollection
            foreach ($path in $request.paths) {
                if ($path -isnot [string] -or [string]::IsNullOrEmpty($path) -or $path.IndexOf([char]0) -ge 0) { throw 'Invalid file' }
                [void](Comparison-Path ($path.Replace('\', '/')))
                [void]$paths.Add($path.Replace('/', '\'))
            }
            $data = New-Object System.Windows.Forms.DataObject
            $data.SetFileDropList($paths)
            [System.Windows.Forms.Clipboard]::SetDataObject($data, $true)
        }
        'types' {
            $data = Clipboard-Object
            $formats = @(Clipboard-Formats $data)
            Write-Text ((@(Recognized $formats)) -join "`n")
        }
        'files' {
            $data = Clipboard-Object
            $formats = @(Clipboard-Formats $data)
            if ($formats -notcontains 'FileDrop') { throw 'No files' }
            $paths = $data.GetData('FileDrop', $true)
            if ($null -eq $paths -or $paths -isnot [string[]]) { throw 'Invalid files' }
            Write-Text (ConvertTo-Json -InputObject @($paths) -Compress)
        }
        'text' {
            $data = Clipboard-Object
            $formats = @(Clipboard-Formats $data)
            if ((@(Recognized $formats)) -notcontains 'text/plain') { throw 'No text' }
            $text = $data.GetData('UnicodeText', $true)
            if ($text -isnot [string]) { throw 'Invalid text' }
            Write-Text $text
        }
        'image' {
            $data = Clipboard-Object
            $formats = @(Clipboard-Formats $data)
            Add-Type -AssemblyName System.Drawing
            $image = $null
            $stream = $null
            $output = $null
            try {
                foreach ($format in @('PNG', 'image/png')) {
                    if ($formats -contains $format) {
                        $value = $data.GetData($format, $false)
                        if ($value -is [byte[]]) { $stream = New-Object System.IO.MemoryStream(,$value) }
                        elseif ($value -is [System.IO.Stream]) { $stream = $value; $stream.Position = 0 }
                        else { throw 'Invalid image' }
                        $image = [System.Drawing.Image]::FromStream($stream, $true, $true)
                        break
                    }
                }
                if ($null -eq $image -and (($formats -contains 'Bitmap') -or ($formats -contains 'DeviceIndependentBitmap'))) {
                    $image = $data.GetData('Bitmap', $true)
                }
                if ($image -isnot [System.Drawing.Image]) { throw 'No image' }
                $output = New-Object System.IO.MemoryStream
                $image.Save($output, [System.Drawing.Imaging.ImageFormat]::Png)
                Write-Header $output.Length
                $output.Position = 0
                $output.CopyTo($stdout)
                $stdout.WriteByte(10)
                $stdout.Flush()
            } finally {
                if ($null -ne $output) { $output.Dispose() }
                if ($null -ne $image) { $image.Dispose() }
                if ($null -ne $stream) { $stream.Dispose() }
            }
        }
        'canonical' { Native; Write-Text ([UcpWindowsNative]::Canonical([string]$request.path)) }
        'overlap' {
            $left = Comparison-Path ([string]$request.left)
            $right = Comparison-Path ([string]$request.right)
            $comparison = [StringComparison]::OrdinalIgnoreCase
            $overlap = [string]::Equals($left, $right, $comparison) -or
                $left.StartsWith($right + '/', $comparison) -or $right.StartsWith($left + '/', $comparison)
            if ($overlap) { Write-Text 'true' } else { Write-Text 'false' }
        }
        'name-key' {
            $name = [string]$request.name
            if (!$name -or $name -match '[\\/\x00]') { throw 'Invalid name' }
            Write-Text ($name.ToUpperInvariant())
        }
        'metadata' {
            $info = Acl-Info ([string]$request.path)
            # Convert inherited access into explicit access before crossing directory ancestry.
            $info.acl.SetAccessRuleProtection($true, $true)
            $sddl = $info.acl.GetSecurityDescriptorSddlForm([System.Security.AccessControl.AccessControlSections]::Access)
            $readonly = ($info.attributes -band [System.IO.FileAttributes]::ReadOnly) -ne 0
            Write-Text (ConvertTo-Json -InputObject @{ sddl = $sddl; readonly = $readonly } -Compress)
        }
        'restore' {
            $path = [string]$request.path
            $info = Acl-Info $path
            if ($request.metadata.sddl -isnot [string] -or $request.metadata.readonly -isnot [bool]) { throw 'Invalid metadata' }
            if ($info.directory) { $acl = New-Object System.Security.AccessControl.DirectorySecurity }
            else { $acl = New-Object System.Security.AccessControl.FileSecurity }
            $acl.SetSecurityDescriptorSddlForm($request.metadata.sddl, [System.Security.AccessControl.AccessControlSections]::Access)
            $acl.SetAccessRuleProtection($true, $true)
            $attributes = $info.attributes -band (-bnot [System.IO.FileAttributes]::ReadOnly)
            if ($request.metadata.readonly) { $attributes = $attributes -bor [System.IO.FileAttributes]::ReadOnly }
            [System.IO.File]::SetAttributes($path, $attributes)
            # Install the DACL last: the restored source may deny attribute writes.
            Set-AclInfo $path $info.directory $acl
        }
        'private' {
            $path = [string]$request.path
            $info = Acl-Info $path
            if ($info.directory) {
                $acl = New-Object System.Security.AccessControl.DirectorySecurity
                $inherit = [System.Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
            } else {
                $acl = New-Object System.Security.AccessControl.FileSecurity
                $inherit = [System.Security.AccessControl.InheritanceFlags]::None
            }
            $acl.SetAccessRuleProtection($true, $false)
            $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
            try { $user = $identity.User } finally { $identity.Dispose() }
            $system = New-Object System.Security.Principal.SecurityIdentifier('S-1-5-18')
            foreach ($sid in @($user, $system)) {
                $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', $inherit, 'None', 'Allow')
                [void]$acl.AddAccessRule($rule)
            }
            Set-AclInfo $path $info.directory $acl
            [System.IO.File]::SetAttributes($path, ($info.attributes -band (-bnot [System.IO.FileAttributes]::ReadOnly)))
        }
        'copy-link' { Native; [UcpWindowsNative]::CopyLink([string]$request.source, [string]$request.target) }
        'move' { Native; [UcpWindowsNative]::Move([string]$request.source, [string]$request.target) }
        default { throw 'Unsupported operation' }
    }
}

# One worker per plugin action: retain loaded assemblies and the native helper,
# but never retain a background service after the action ends.
while ($null -ne ($line = [Console]::In.ReadLine())) {
    $script:responded = $false
    try {
        Invoke-Request ($line | ConvertFrom-Json)
        if (!$script:responded) { Write-Text '' }
    } catch {
        # A partial binary response cannot be followed by another frame.
        if ($script:responded) { exit 1 }
        [Console]::Write("ERR`n")
        [Console]::Out.Flush()
    }
}
]=]

local ERRORS = {
	copy = "Windows file clipboard write failed",
	types = "Windows clipboard format inspection failed",
	files = "Windows file clipboard read failed",
	text = "Windows text clipboard read failed",
	image = "Windows image clipboard read failed",
	canonical = "Windows physical path resolution failed",
	overlap = "Windows path comparison failed",
	["name-key"] = "Windows filename comparison failed",
	metadata = "Windows access metadata capture failed",
	restore = "Windows access metadata restoration failed",
	private = "Windows private access setup failed",
	["copy-link"] = "Windows link cloning failed",
	move = "Windows no-replace rename failed",
}

local child

local function terminate()
	local process = child
	child = nil
	if process then
		process:start_kill()
		process:wait()
	end
end

function M.close()
	local process = child
	child = nil
	if process then
		-- Closing stdin ends the request loop before waiting for process exit.
		process:wait_with_output()
	end
end

function M.run(operation, request)
	local failure = ERRORS[operation]
	if not failure then
		return nil, "Unsupported Windows backend operation"
	end
	local encoded, payload = pcall(ya.json_encode, { operation = operation, request = request or {} })
	if not encoded or type(payload) ~= "string" then
		return nil, "Windows request encoding failed"
	end
	if not child then
		child = Command("powershell.exe")
			:arg({ "-NoLogo", "-NoProfile", "-NonInteractive", "-STA", "-Command", SCRIPT })
			:stdin(Command.PIPED)
			:stdout(Command.PIPED)
			:stderr(Command.PIPED)
			:spawn()
		if not child then
			return nil, "Windows PowerShell could not be started"
		end
	end
	if not child:write_all(payload) or not child:write_all("\n") or not child:flush() then
		terminate()
		return nil, "Windows request transport failed"
	end
	local header, event = child:read_line()
	if event == 0 and (header == "ERR\n" or header == "ERR\r\n") then
		return nil, failure
	end
	local size = event == 0 and header and header:match("^OK (%d+)\r?\n$")
	local remaining = size and math.tointeger(tonumber(size))
	if not remaining or remaining < 0 then
		terminate()
		return nil, failure
	elseif remaining == 0 then
		return ""
	end
	-- read_line preserves raw bytes as Lua strings; Child:read exposes byte
	-- arrays. A final newline terminates even binary payloads without newlines.
	remaining = remaining + 1
	local chunks
	while remaining > 0 do
		local data, source = child:read_line()
		if source ~= 0 or not data or #data == 0 or #data > remaining then
			terminate()
			return nil, failure
		end
		if #data == remaining then
			if data:sub(-1) ~= "\n" then
				terminate()
				return nil, failure
			end
			local tail = data:sub(1, -2)
			if not chunks then
				return tail
			elseif tail ~= "" then
				chunks[#chunks + 1] = tail
			end
			return table.concat(chunks)
		end
		chunks = chunks or {}
		chunks[#chunks + 1] = data
		remaining = remaining - #data
	end
end

return M
