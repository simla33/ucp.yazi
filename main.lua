--[[
Universal Copy/Paste for Yazi

    plugin ucp -- copy [notify]
    plugin ucp -- paste [notify]

Native cuts use Yazi's paste command. Otherwise, external file URLs, images,
and text take precedence over internal copied files. Unsupported clipboard
data falls back to native yank state. See README.md for collision semantics,
staging/recovery guarantees, platform requirements, and verification limits.
]]

local M = {}
local PackageName = "ucp"
local windows = ya.target_os() == "windows" and require("ucp.windows") or nil

--==============================================================================
-- Helpers BEGIN
--==============================================================================

---@enum STATE
local STATE = {
	INPUT_POSITION = "input_position",
	OVERWRITE_CONFIRM_POSITION = "overwrite_confirm_position",
	HIDE_NOTIFY = "hide_notify",
}

local set_state = ya.sync(function(state, key, value)
	if state then
		state[key] = value
	else
		state = {}
		state[key] = value
	end
end)

local get_state = ya.sync(function(state, key)
	if state then
		return state[key]
	else
		return nil
	end
end)

local function warn(s, ...)
	if get_state(STATE.HIDE_NOTIFY) then
		return
	end
	ya.notify({ title = PackageName, content = string.format(s, ...), timeout = 3, level = "warn" })
end

--==============================================================================
-- Helpers END
--==============================================================================

--==============================================================================
-- Entry BEGIN
--==============================================================================

function M:entry(job)
	local action = job.args[1]

	ya.dbg("Action", tostring(action), table.concat(job.args or {}, ", "))

	if not action then
		ya.err("No action, defaulting to paste")
		return M:paste_entry(job)
	end

	if action == "copy" then
		ya.dbg("Calling copy_entry")
		return M:copy_entry(job)
	elseif action == "paste" then
		ya.dbg("Calling paste_entry")
		return M:paste_entry(job)
	else
		warn("Unknown action, requires copy or paste")
		return
	end
end

function M:setup(opts)
	if opts and opts.hide_notify and type(opts.hide_notify) == "boolean" then
		set_state(STATE.HIDE_NOTIFY, opts.hide_notify)
	else
		set_state(STATE.HIDE_NOTIFY, false)
	end
	if opts and opts.input_position and type(opts.input_position) == "table" then
		set_state(STATE.INPUT_POSITION, opts.input_position)
	else
		set_state(STATE.INPUT_POSITION, { "center", w = 70 })
	end
	if opts and opts.overwrite_confirm_position and type(opts.overwrite_confirm_position) == "table" then
		set_state(STATE.OVERWRITE_CONFIRM_POSITION, opts.overwrite_confirm_position)
	else
		set_state(STATE.OVERWRITE_CONFIRM_POSITION, { "center", w = 70, h = 10 })
	end
end

--==============================================================================
-- Entry END
--==============================================================================

--==============================================================================
-- Copy BEGIN
--==============================================================================

-- Export only local paths; keep virtual URLs intact in Yazi's native selection.
local selected_or_hovered = ya.sync(function()
	local tab, paths = cx.active, {}
	for _, u in pairs(tab.selected) do
		paths[#paths + 1] = u.url
	end
	if #paths == 0 and tab.current.hovered then
		paths[1] = tab.current.hovered.url
	end
	for i, url in ipairs(paths) do
		local spec = url.spec
		if not (spec.is_regular or spec.is_search) then
			return nil
		end
		paths[i] = tostring(url.path)
	end
	return paths
end)

local function command_output(program, args)
	local output = Command(program):arg(args):output()
	if output and output.status.success then
		return output.stdout
	end
end

local function command_input(program, args, content)
	local child, err = Command(program):arg(args):stdin(Command.PIPED):spawn()
	if not child then
		return nil, err
	end
	local ok
	ok, err = child:write_all(content)
	if ok then
		ok, err = child:flush()
	end
	if not ok then
		child:start_kill()
		child:wait()
		return nil, err
	end
	-- wait closes stdin after flushing, allowing the clipboard owner to detach.
	return child:wait()
end

local function linux_backends()
	if os.getenv("WAYLAND_DISPLAY") then
		return { "wayland", "x11" }
	end
	return { "x11", "wayland" }
end

-- A plain JS array turns each NSURL into an NSDictionary, which writeObjects rejects.
-- Reading back once keeps every item; otherwise only the first survives osascript exiting.
local MAC_COPY = [[
ObjC.import('AppKit');
function run() {
    var data = $.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile;
    var text = ObjC.unwrap($.NSString.alloc.initWithDataEncoding(data, $.NSUTF8StringEncoding));
    var urls = $.NSMutableArray.array;
    text.split(/\r?\n/).filter(function(s) { return s.length > 0; })
        .forEach(function(s) { urls.addObject($.NSURL.URLWithString(s)); });
    var pb = $.NSPasteboard.generalPasteboard;
    pb.clearContents;
    if (!pb.writeObjects(urls)) throw new Error('Cannot write file URLs to clipboard');
    pb.readObjectsForClassesOptions($.NSArray.arrayWithObject($.NSURL), $.NSDictionary.dictionary);
}
]]

function M:copy_entry(job)
	local show_notify = false
	for _, arg in ipairs(job.args or {}) do
		show_notify = show_notify or arg == "notify"
	end
	ya.emit("escape", { visual = true })
	local paths = selected_or_hovered()
	if paths and #paths == 0 then
		if show_notify then
			warn("No file selected")
		end
		return
	end
	ya.emit("yank", {})
	if not paths then
		warn("External copy supports local files and search results only; native yank retained. Use Yazi's built-in paste for virtual files.")
		return
	end

	local status, err
	if windows then
		local result
		result, err = windows.run("copy", { paths = paths })
		status = { success = result ~= nil }
	else
		local uris = {}
		for _, path in ipairs(paths) do
			uris[#uris + 1] = "file://" .. path:gsub("([^A-Za-z0-9%-%._~/])", function(c)
				return string.format("%%%02X", c:byte())
			end)
		end
		local content = table.concat(uris, "\r\n") .. "\r\n"
		status, err = command_input("osascript", { "-l", "JavaScript", "-e", MAC_COPY }, content)
		if not status or not status.success then
			for _, backend in ipairs(linux_backends()) do
				if backend == "wayland" then
					status, err = command_input("wl-copy", { "--type", "text/uri-list" }, content)
				else
					status, err = command_input("xclip", { "-selection", "clipboard", "-t", "text/uri-list" }, content)
				end
				if status and status.success then
					break
				end
			end
		end
	end
	if show_notify then
		ya.notify({
			title = PackageName,
			content = status and status.success and "Successfully copied the file(s) to system clipboard"
				or ("Could not copy selected file(s): " .. tostring(err or (status and status.code))),
			level = status and status.success and "info" or "error",
			timeout = 5,
		})
	end
end

--==============================================================================
-- Copy END
--==============================================================================

--==============================================================================
-- Paste Helpers BEGIN
--==============================================================================

local get_cwd = ya.sync(function()
	return tostring(cx.active.current.cwd)
end)

local get_current_tab_id = ya.sync(function()
	return tostring(cx.active.id.value)
end)

--==============================================================================
-- Paste Helpers END
--==============================================================================

--==============================================================================
-- Paste Images BEGIN
--==============================================================================

local MAC_TYPES = [[
ObjC.import('AppKit');
function run() {
    var pb = $.NSPasteboard.generalPasteboard;
    var types = pb.types;
    if (!types) return '';
    var result = [];
    if (types.containsObject('public.file-url') || types.containsObject('NSFilenamesPboardType'))
        result.push('text/uri-list');
    if (types.containsObject('public.png') || types.containsObject('public.tiff') ||
        types.containsObject('public.jpeg') || types.containsObject('com.compuserve.gif') ||
        types.containsObject('Apple PNG pasteboard type') || types.containsObject('NeXT TIFF v4.0 pasteboard type'))
        result.push('image/png');
    if (types.containsObject('public.utf8-plain-text') || types.containsObject('public.utf16-plain-text') ||
        types.containsObject('public.utf16-external-plain-text') || types.containsObject('NSStringPboardType'))
        result.push('text/plain');
    return result.join('\n');
}
]]

local function mime_lines(content)
	local result = {}
	for line in (content or ""):gmatch("[^\r\n]+") do
		result[#result + 1] = line
	end
	return #result > 0 and result or nil
end

local function get_clipboard_mimetypes()
	if windows then
		return mime_lines(windows.run("types", {})), "windows"
	end
	local types = mime_lines(command_output("osascript", { "-l", "JavaScript", "-e", MAC_TYPES }))
	if types then
		return types, "mac"
	end
	for _, backend in ipairs(linux_backends()) do
		local content
		if backend == "wayland" then
			content = command_output("wl-paste", { "--list-types" })
		else
			content = command_output("xclip", { "-selection", "clipboard", "-o", "-t", "TARGETS" })
		end
		types = mime_lines(content)
		if types then
			return types, backend
		end
	end
end

local function read_clipboard(mime, backend)
	if backend == "windows" then
		return windows.run(mime == "text/plain" and "text" or "image", {})
	elseif backend == "wayland" then
		return command_output("wl-paste", { "--no-newline", "--type", mime })
	elseif backend == "x11" then
		return command_output("xclip", { "-selection", "clipboard", "-o", "-t", mime })
	elseif backend == "mac" and mime == "text/plain" then
		return command_output("pbpaste", {})
	end
end

local MAC_IMAGE = [[
ObjC.import('AppKit');
function run(argv) {
    var pb = $.NSPasteboard.generalPasteboard;
    var images = pb.readObjectsForClassesOptions([$.NSImage], {});
    if (!images || images.count < 1) throw new Error('No clipboard image');
    var rep = $.NSBitmapImageRep.imageRepWithData(images.objectAtIndex(0).TIFFRepresentation);
    if (!rep) throw new Error('Cannot decode clipboard image');
    var data = rep.representationUsingTypeProperties($.NSBitmapImageFileTypePNG, {});
    if (!data || !data.writeToFileAtomically(argv[0], true))
        throw new Error('Cannot write clipboard image');
}
]]

local function get_clipboard_image_data(format, backend)
	if backend ~= "mac" then
		return read_clipboard(format == "svg" and "image/svg+xml" or ("image/" .. format), backend)
	end
	local temp_dir = os.getenv("TMPDIR") or "/tmp"
	local output = command_output("mktemp", { "-d", temp_dir .. "/ucp-clipboard.XXXXXX" })
	if not output or output:sub(-1) ~= "\n" then
		warn("Could not create a private clipboard image directory")
		return
	end
	-- mktemp prints one protocol newline; embedded pathname newlines are data.
	local temp_root = output:sub(1, -2)
	local temp_file = temp_root .. "/image.png"
	local status = Command("osascript"):arg({ "-l", "JavaScript", "-e", MAC_IMAGE, temp_file }):status()
	local data
	if status and status.success then
		local file = io.open(temp_file, "rb")
		if file then
			data = file:read("*a")
			file:close()
		end
	end
	local removed, err = fs.remove("dir_all", Url(temp_root))
	if not removed then
		warn("Could not remove temporary clipboard image directory: %s", tostring(err))
	end
	return data
end

function M:handle_image_paste(image_targets, no_hover, backend)
	local format = image_targets:match("image/svg%+xml") and "svg" or image_targets:match("image/([%w-]+)")
	if backend == "mac" then
		format = "png"
	end
	if not format then
		warn("Could not determine clipboard image format")
		return false
	end
	local data = get_clipboard_image_data(format, backend)
	if not data or #data == 0 then
		warn("Failed to get image data from clipboard")
		return false
	end
	local file_name = string.format("pasted_%s.%s", os.date("%Y-%m-%d_%H-%M-%S"), format)
	return M:save_file_with_conflict_handling(file_name, data, no_hover)
end

--==============================================================================
-- Paste Images END
--==============================================================================

--==============================================================================
-- Paste List BEGIN
--==============================================================================

local MAC_FILES = [[
ObjC.import('AppKit');
function run() {
    var pb = $.NSPasteboard.generalPasteboard;
    var options = {};
    options[ObjC.unwrap($.NSPasteboardURLReadingFileURLsOnlyKey)] = true;
    var urls = pb.readObjectsForClassesOptions([$.NSURL], options);
    var result = [];
    if (urls) {
        for (var i = 0; i < urls.count; i++)
            result.push(ObjC.unwrap(urls.objectAtIndex(i).absoluteString));
    }
    if (!result.length) {
        var paths = pb.propertyListForType('NSFilenamesPboardType');
        if (paths) {
            for (var j = 0; j < paths.count; j++)
                result.push(ObjC.unwrap($.NSURL.fileURLWithPath(paths.objectAtIndex(j)).absoluteString));
        }
    }
    return result.join('\n');
}
]]

local function decode_file_urls(content)
	local paths = {}
	-- Split the encoded representation, never decoded POSIX paths.
	for line in (content or ""):gmatch("[^\r\n]+") do
		local host, path = line:match("^file://([^/]*)(/.*)$")
		if path and (host == "" or host == "localhost") then
			path = path:gsub("%%(%x%x)", function(hex)
				return string.char(tonumber(hex, 16))
			end)
			if not path:find("\0", 1, true) then
				paths[#paths + 1] = path
			end
		end
	end
	return #paths > 0 and paths or nil
end

local function get_clipboard_file_uris(mimetypes, backend)
	if backend == "windows" then
		local content = windows.run("files", {})
		local paths = content and ya.json_decode(content)
		return type(paths) == "table" and #paths > 0 and paths or nil
	elseif backend == "mac" then
		return decode_file_urls(command_output("osascript", { "-l", "JavaScript", "-e", MAC_FILES }))
	end
	for _, mime in ipairs({ "code/file-list", "text/uri-list" }) do
		for _, available in ipairs(mimetypes) do
			if available == mime then
				local paths = decode_file_urls(read_clipboard(mime, backend))
				if paths then
					return paths
				end
			end
		end
	end
end

-- Filesystem transfers use structured lstat snapshots, never shell traversal.
local function get_filename_from_path(path)
	return windows and Url(path).name or path:gsub("/+$", ""):match("[^/]+$")
end

local function transfer_stat(url)
	local cha, err = fs.cha(url, false)
	if cha or (err and err.kind == "NotFound") then
		return cha
	end
	return nil, string.format("Cannot inspect %s: %s", tostring(url), tostring(err))
end

local function windows_component(name)
	if name == "" or name:find('[%z\1-\31<>:"|?*]') or name:find("[%. ]$") then
		return false
	end
	local stem = name:match("^([^.]*)"):gsub(" +$", ""):upper()
	return stem ~= "CON" and stem ~= "PRN" and stem ~= "AUX" and stem ~= "NUL"
		and stem ~= "CONIN$" and stem ~= "CONOUT$" and stem ~= "CLOCK$"
		and not stem:match("^COM[1-9]$") and not stem:match("^LPT[1-9]$")
		and not ((stem:sub(1, 3) == "COM" or stem:sub(1, 3) == "LPT")
			and (stem:sub(4) == "¹" or stem:sub(4) == "²" or stem:sub(4) == "³"))
end

local function absolute_path(path)
	if windows then
		if type(path) ~= "string" then
			return nil, "Expected an absolute Windows path"
		end
		path = path:gsub("\\", "/")
		-- Accept only filesystem extended paths, never device namespaces.
		if path:match("^//%?/UNC/") then
			path = "//" .. path:sub(9)
		elseif path:match("^//%?/[A-Za-z]:/") then
			path = path:sub(5)
		end
		local root, rest = path:match("^([A-Za-z]:/)(.*)$")
		if not root then
			root, rest = path:match("^(//[^/]+/[^/]+)(.*)$")
			if root then
				for component in root:gmatch("[^/]+") do
					if not windows_component(component) then
						return nil, "Invalid Windows share root"
					end
				end
			end
		end
		if not root then
			return nil, "Expected an absolute drive or UNC path"
		end
		for component in rest:gmatch("[^/]+") do
			if not windows_component(component) then
				return nil, "Invalid Windows path component"
			end
		end
		rest = rest:gsub("/+", "/"):gsub("^/", ""):gsub("/$", "")
		return root .. (rest == "" and "" or ((root:sub(-1) == "/" and "" or "/") .. rest))
	end
	if type(path) ~= "string" or path:sub(1, 1) ~= "/" or path:find("\0", 1, true) then
		return nil, "Expected an absolute local path without NUL bytes"
	end
	for part in path:gmatch("[^/]+") do
		if part == "." or part == ".." then
			return nil, "Dot path components are not allowed"
		end
	end
	-- Trailing slashes must not turn lstat of a directory link into stat.
	return path == "/" and path or path:gsub("/+$", "")
end

local function canonical_path(url)
	if windows then
		local out, err = windows.run("canonical", { path = tostring(url) })
		if not out then
			return nil, err
		end
		return absolute_path(out)
	end
	local out = command_output("realpath", { tostring(url) })
	if not out or out:sub(-1) ~= "\n" then
		return nil, "Cannot canonicalize " .. tostring(url)
	end
	-- Remove one protocol newline, not whitespace belonging to the filename.
	out = out:sub(1, -2)
	if out:sub(1, 1) ~= "/" then
		return nil, "Invalid canonical path for " .. tostring(url)
	end
	return out
end

local function paths_overlap(a, b)
	if windows then
		local result, err = windows.run("overlap", { left = a, right = b })
		if result ~= "true" and result ~= "false" then
			return nil, err or "Cannot compare canonical Windows paths"
		end
		return result == "true"
	end
	local function contains(parent, child)
		return parent == child or (parent == "/" and child:sub(1, 1) == "/")
			or child:sub(1, #parent + 1) == parent .. "/"
	end
	return contains(a, b) or contains(b, a)
end

-- Missing components are permitted for text filenames, but neither links
-- nor stat errors are. Check the root and all existing intermediate nodes.
local function destination_safe(url)
	local current = url
	while current do
		local cha, err = transfer_stat(current)
		if err then
			return false, err
		elseif cha and (cha.is_link or (windows and cha.is_indirect)) then
			return false, "Destination contains a symlink: " .. tostring(current)
		elseif current ~= url and cha and not cha.is_dir then
			return false, "Destination parent is not a directory: " .. tostring(current)
		end
		current = current.parent
	end
	return true
end

local function destination_cwd()
	local chosen = Url(get_cwd())
	local cha, err = transfer_stat(chosen)
	if not cha or cha.is_link or (windows and cha.is_indirect) or not cha.is_dir then
		return nil, err or ("Destination root must be a real directory: " .. tostring(chosen))
	end
	-- Resolve aliases above the chosen directory (e.g. macOS /var), not links
	-- encountered inside the destination tree.
	local physical, canonical_err = canonical_path(chosen)
	if not physical then
		return nil, canonical_err
	end
	local cwd = Url(physical)
	local safe, safe_err = destination_safe(cwd)
	if not safe then
		return nil, safe_err
	end
	return cwd
end

-- Missing or dangling-link leaves use their canonical existing parent.
-- No failed canonicalization is replaced by an unchecked raw path.
local function canonical_leaf(url, cha)
	if cha and not (cha.is_link or (windows and cha.is_indirect)) then
		return canonical_path(url)
	end
	local suffix, current = {}, url
	while current do
		local info, err = transfer_stat(current)
		if err then
			return nil, err
		end
		if info and not (current == url and (info.is_link or (windows and info.is_indirect))) then
			local base, cerr = canonical_path(current)
			if not base then
				return nil, cerr
			end
			for i = #suffix, 1, -1 do
				base = (base:sub(-1) == "/" and base or base .. "/") .. suffix[i]
			end
			return base
		end
		if not current.name then
			break
		end
		suffix[#suffix + 1] = current.name
		current = current.parent
	end
	return nil, "Cannot canonicalize " .. tostring(url)
end

local function transfer_overlap(source, source_cha, target, target_cha)
	local src, err = canonical_leaf(source, source_cha)
	if not src then
		return false, err
	end
	local dst, derr = canonical_leaf(target, target_cha)
	if not dst then
		return false, derr
	end
	local overlap, overlap_err = paths_overlap(src, dst)
	if overlap == nil then
		return false, overlap_err
	elseif overlap then
		return false, "Source and destination overlap: " .. tostring(target)
	end
	-- Also detect resolvable source-link aliases without traversing them
	-- during copying or requiring realpath of dangling links.
	if source_cha.is_link or (windows and source_cha.is_indirect) then
		local followed, follow_err = fs.cha(source, true)
		local dangling = follow_err
			and (follow_err.kind == "NotFound" or follow_err.kind == "NotADirectory")
		if not followed and not dangling then
			return false, "Cannot inspect source-link alias: " .. tostring(follow_err)
		elseif followed then
			local resolved, rerr = canonical_path(source)
			if not resolved then
				return false, rerr
			end
			overlap, overlap_err = paths_overlap(resolved, dst)
			if overlap == nil then
				return false, overlap_err
			elseif overlap then
				return false, "Source and destination overlap through a link: " .. tostring(target)
			end
		end
	end
	return true
end

local function transfer_kind(cha)
	if cha.is_link or (windows and cha.is_indirect) then
		return "link"
	elseif cha.is_dir then
		return "dir"
	elseif cha.is_block or cha.is_char or cha.is_fifo or cha.is_sock or cha.is_dummy then
		return nil
	end
	return "file"
end

-- Preserve rwx bits so a staged swap never widens private data.
local function transfer_mode(cha, url)
	if windows then
		local content, err = windows.run("metadata", { path = tostring(url) })
		local mode = content and ya.json_decode(content)
		if type(mode) ~= "table" or type(mode.sddl) ~= "string" or type(mode.readonly) ~= "boolean" then
			return nil, err or "Cannot read Windows access permissions"
		end
		return mode
	end
	local perm = cha:perm()
	if type(perm) ~= "string" or #perm < 10 then
		return cha.is_dir and "0700" or "0600"
	end
	local digits = {}
	for group = 0, 2 do
		local offset, digit = 2 + group * 3, 0
		if perm:sub(offset, offset) == "r" then
			digit = digit + 4
		end
		if perm:sub(offset + 1, offset + 1) == "w" then
			digit = digit + 2
		end
		local exec = perm:sub(offset + 2, offset + 2)
		if exec == "x" or exec == "s" or exec == "t" then
			digit = digit + 1
		end
		digits[#digits + 1] = tostring(digit)
	end
	return "0" .. table.concat(digits)
end

local function transfer_chmod(url, mode)
	if windows then
		local operation = type(mode) == "table" and "restore" or "private"
		local result, err = windows.run(operation, { path = tostring(url), metadata = mode })
		return result ~= nil, err
	end
	if command_output("chmod", { mode, tostring(url) }) == nil then
		return false, "Cannot set permissions on " .. tostring(url)
	end
	return true
end

local function snapshot_tree(url, cha)
	local kind = transfer_kind(cha)
	if not kind then
		return nil, "Unsupported source type: " .. tostring(url)
	end
	local mode, mode_err
	if kind ~= "link" then
		mode, mode_err = transfer_mode(cha, url)
		if not mode then
			return nil, mode_err
		end
	end
	local node = { url = url, kind = kind, mode = mode }
	if kind == "dir" then
		node.children = {}
		local files, err = fs.read_dir(url, { resolve = false })
		if not files then
			return nil, string.format("Cannot read directory %s: %s", tostring(url), tostring(err))
		end
		for _, file in ipairs(files) do
			local name = file.url.name
			if not name or name == "." or name == ".." or name:find("/", 1, true) then
				return nil, "Invalid directory entry in " .. tostring(url)
			elseif windows and not windows_component(name) then
				return nil, "Invalid Windows directory entry"
			end
			local child_cha, stat_err = transfer_stat(file.url)
			if not child_cha then
				return nil, stat_err or ("Source disappeared: " .. tostring(file.url))
			end
			local child, cerr = snapshot_tree(file.url, child_cha)
			if not child then
				return nil, cerr
			end
			local key = name
			if windows then
				key, cerr = windows.run("name-key", { name = name })
				if not key then
					return nil, cerr
				elseif node.children[key] then
					return nil, "Ambiguous case-insensitive Windows directory entries"
				end
			end
			child.name = name
			node.children[key] = child
		end
	end
	return node
end

-- Only writes into private staging. Links are leaves, including dangling
-- links; fs.copy never writes onto a preexisting user path.
local function stage_tree(node, destination)
	if node.kind == "dir" then
		local ok, err = fs.create("dir", destination)
		if not ok then
			return false, tostring(err)
		end
		for name, child in pairs(node.children) do
			local copied, cerr = stage_tree(child, destination:join(child.name or name))
			if not copied then
				return false, cerr
			end
		end
		return transfer_chmod(destination, node.mode)
	elseif node.kind == "link" then
		if windows then
			local result, err = windows.run("copy-link", { source = tostring(node.url), target = tostring(destination) })
			return result ~= nil, err
		end
		if command_output("cp", { "-P", "--", tostring(node.url), tostring(destination) }) == nil then
			return false, "Cannot copy source link: " .. tostring(node.url)
		end
		return true
	elseif node.kind == "data" then
		local ok, err = fs.write(destination, node.data)
		if not ok then
			return false, tostring(err)
		end
	else
		local len, err = fs.copy(node.url, destination)
		if len == nil then
			return false, string.format("Cannot copy %s: %s", tostring(node.url), tostring(err))
		end
	end
	return transfer_chmod(destination, node.mode)
end

-- Merge is a tree plan, not in-place mutation. Reject incoming collisions
-- with destination links; preserve unrelated links in the staged result.
local function merge_child(parent, name, incoming)
	local existing = parent.children[name]
	if existing and existing.kind == "link" then
		return false, "Destination collision is a symlink: " .. tostring(existing.url)
	elseif existing and existing.kind == "dir" then
		if incoming.kind == "dir" then
			for child_name, child in pairs(incoming.children) do
				local ok, err = merge_child(existing, child_name, child)
				if not ok then
					return false, err
				end
			end
			return true
		end
		return merge_child(existing, name, incoming)
	end
	parent.children[name] = incoming
	return true
end

local function copy_destination(target, kind)
	local name, extension = target.name, ""
	if kind ~= "dir" then
		local stem, ext = name:match("^(.+)(%.[^.]*)$")
		if stem then
			name, extension = stem, ext
		end
	end
	local index = 0
	while true do
		local suffix = index == 0 and "" or ("_" .. index)
		local candidate = target.parent:join(name .. "_copy" .. suffix .. extension)
		local cha, err = transfer_stat(candidate)
		if err then
			return nil, err
		elseif not cha then
			return candidate
		end
		index = index + 1
	end
end

local function cleanup_stage(stage)
	-- Read-only directory modes belong on installed trees, not discarded
	-- private copies/backups. Unlock only real directories inside staging;
	-- never chmod or traverse preserved source links or restored targets.
	local function unlock(url)
		local cha, stat_err = transfer_stat(url)
		if not cha then
			return false, stat_err or ("Missing staging entry: " .. tostring(url))
		elseif cha.is_link or (windows and cha.is_indirect) or (not windows and not cha.is_dir) then
			return true
		end
		local secured, secure_err = transfer_chmod(url, "0700")
		if not secured then
			return false, secure_err
		end
		if not cha.is_dir then
			return true
		end
		local files, read_err = fs.read_dir(url, { resolve = false })
		if not files then
			return false, tostring(read_err)
		end
		for _, file in ipairs(files) do
			local ok, err = unlock(file.url)
			if not ok then
				return false, err
			end
		end
		return true
	end
	local unlocked, unlock_err = unlock(stage)
	if not unlocked then
		warn("Cannot clean private staging directory %s: %s", tostring(stage), tostring(unlock_err))
		return
	end
	local ok, err = fs.remove("dir_all", stage)
	if not ok then
		warn("Cannot clean private staging directory %s: %s", tostring(stage), tostring(err))
	end
end

local function transfer_rename(source, target)
	if windows then
		local result, err = windows.run("move", { source = tostring(source), target = tostring(target) })
		return result ~= nil, err
	end
	return fs.rename(source, target)
end

-- Stage completely before mutation. Reserve fresh destinations exclusively;
-- keep existing targets in a private backup until the final rename succeeds.
-- A failed rollback deliberately retains the recovery directory.
local function install_tree(tree, target, old_cha)
	local stage, err = fs.unique("dir", target.parent:join(".ucp-stage"))
	if not stage then
		return nil, tostring(err)
	end
	local secured, serr = transfer_chmod(stage, "0700")
	if not secured then
		cleanup_stage(stage)
		return nil, serr
	end
	local payload, backup = stage:join("payload"), stage:join("backup")
	local ready, rerr = stage_tree(tree, payload)
	if not ready then
		cleanup_stage(stage)
		return nil, rerr
	end
	local safe, safe_err = destination_safe(old_cha and target or target.parent)
	if not safe then
		cleanup_stage(stage)
		return nil, safe_err
	end
	local reserved
	if old_cha then
		local moved, merr = transfer_rename(target, backup)
		if not moved then
			cleanup_stage(stage)
			return nil, "Cannot back up destination: " .. tostring(merr)
		end
	else
		reserved, err = fs.unique(tree.kind == "dir" and "dir" or "file", target)
		if not reserved then
			cleanup_stage(stage)
			return nil, "Cannot reserve unused destination: " .. tostring(err)
		end
		target = reserved
	end
	if windows and reserved then
		-- Windows cannot rename a directory over its empty reservation.
		-- Release only our placeholder, then use a no-replace native rename.
		local removed, remove_err = fs.remove(tree.kind == "dir" and "dir" or "file", reserved)
		if not removed then
			cleanup_stage(stage)
			return nil, "Cannot release reserved destination: " .. tostring(remove_err)
		end
		reserved = nil
	end
	local installed, ierr = transfer_rename(payload, target)
	if not installed then
		if old_cha then
			local restored, restore_err = transfer_rename(backup, target)
			if not restored then
				return nil, string.format(
					"Install failed (%s); rollback failed (%s). Original target retained at %s",
					tostring(ierr), tostring(restore_err), tostring(backup)
				)
			end
		elseif reserved then
			local removed, remove_err = fs.remove(tree.kind == "dir" and "dir" or "file", reserved)
			if not removed then
				warn("Cannot remove reserved destination %s: %s", tostring(reserved), tostring(remove_err))
			end
		end
		cleanup_stage(stage)
		return nil, "Cannot install destination: " .. tostring(ierr)
	end
	cleanup_stage(stage)
	return target
end

local function file_target(target, name)
	while true do
		local safe, err = destination_safe(target)
		if not safe then
			return nil, nil, err
		end
		local cha, cerr = transfer_stat(target)
		if cerr then
			return nil, nil, cerr
		elseif not cha or not cha.is_dir then
			return target, cha
		end
		target = target:join(name)
	end
end

local function collision_action(kind, name, index, total)
	local candidates, actions = {}, {}
	local function offer(key, description, action)
		local at = #candidates + 1
		candidates[at] = { on = key, desc = description }
		actions[at] = action
	end
	offer("|", (kind == "dir" and "Directory: " or "File: ") .. name, "skip")
	offer("|", string.format("Progress: %d/%d", index, total), "skip")
	offer("|", "", "skip")
	offer("o", "Overwrite", "overwrite")
	if kind == "dir" then
		offer("m", "Merge contents", "merge")
	end
	offer("c", "Create copy (_copy)", "copy")
	offer("q", kind == "dir" and "Skip directory" or "Skip file", "skip")
	offer("O", "Overwrite All", "overwrite_all")
	offer("C", "Create copy All", "copy_all")
	offer("Q", "Cancel All", "cancel_all")
	local selected = ya.which({ cands = candidates })
	return selected and actions[selected] or "skip"
end

local function reveal_transfer(url, no_hover)
	if not no_hover then
		ya.emit("reveal", { tostring(url), tab = get_current_tab_id(), no_dummy = true, raw = true })
	end
end

function M:handle_file_list_paste(file_uris, no_hover)
	local outcome = { pasted = 0, skipped = 0, failed = 0, cancelled = false }
	local cwd, root_err = destination_cwd()
	if not cwd then
		warn("%s", root_err)
		outcome.failed = #file_uris
		return outcome
	end
	local bulk_action
	for index, raw in ipairs(file_uris) do
		local function paste_item()
			local path, perr = absolute_path(raw)
			if not path then
				return nil, perr
			end
			local source = Url(path)
			local cha, cerr = transfer_stat(source)
			if not cha then
				return nil, cerr or ("Source does not exist: " .. path)
			end
			local kind, name = transfer_kind(cha), get_filename_from_path(path)
			if not kind or not name then
				return nil, "Unsupported source: " .. path
			end
			local target = cwd:join(name)
			local safe, serr = destination_safe(target)
			if not safe then
				return nil, serr
			end
			local target_cha, terr = transfer_stat(target)
			if terr then
				return nil, terr
			end
			local action = "overwrite"
			if target_cha then
				action = bulk_action or collision_action(kind, name, index, #file_uris)
				if action == "cancel_all" or action == "skip" then
					return action
				elseif action == "overwrite_all" then
					bulk_action, action = "overwrite", "overwrite"
				elseif action == "copy_all" then
					bulk_action, action = "copy", "copy"
				end
			end
			if action == "copy" then
				local copy, copy_err = copy_destination(target, kind)
				if not copy then
					return nil, copy_err
				end
				target, target_cha = copy, nil
			elseif kind ~= "dir" and target_cha and target_cha.is_dir then
				local final, final_cha, ferr = file_target(target, name)
				if not final then
					return nil, ferr
				end
				target, target_cha = final, final_cha
			end
			local separate, oerr = transfer_overlap(source, cha, target, target_cha)
			if not separate then
				return nil, oerr
			end
			local tree, tree_err = snapshot_tree(source, cha)
			if not tree then
				return nil, tree_err
			end
			if action == "merge" and target_cha and target_cha.is_dir then
				local merged, merr = snapshot_tree(target, target_cha)
				if not merged then
					return nil, merr
				end
				for child_name, child in pairs(tree.children) do
					local ok, err = merge_child(merged, child_name, child)
					if not ok then
						return nil, err
					end
				end
				tree = merged
			end
			return install_tree(tree, target, target_cha)
		end
		local result, err = paste_item()
		if result == "cancel_all" then
			outcome.skipped = outcome.skipped + #file_uris - index + 1
			outcome.cancelled = true
			break
		elseif result == "skip" then
			outcome.skipped = outcome.skipped + 1
		elseif result then
			outcome.pasted = outcome.pasted + 1
			reveal_transfer(result, no_hover)
		else
			outcome.failed = outcome.failed + 1
			warn("Failed to paste item %d/%d: %s", index, #file_uris, err)
		end
	end
	return outcome
end

--==============================================================================
-- Paste List END
--==============================================================================

--==============================================================================
-- Paste Text To File BEGIN
--==============================================================================

local function input_file_name(default_name)
	local pos = get_state(STATE.INPUT_POSITION)
	pos = pos or { "center", w = 70 }

	local input_value, input_event = ya.input({
		title = "Paste into a new file. Enter file name:",
		value = default_name or "",
		pos = pos,
	})
	if input_event == 1 then
		if not input_value or input_value == "" then
			warn("File name can't be empty!")
			return false
		elseif input_value:match("/$") then
			warn("File name cannot end with '/'")
			return false
		end
		return input_value
	end
end

-- Handle text paste (text/plain mimetype) - suggest creating a new file
function M:handle_text_paste(clipboard_content, no_hover)
	local file_name = input_file_name()
	if not file_name then
		return file_name -- nil: cancelled; false: invalid input
	end
	return M:save_file_with_conflict_handling(file_name, clipboard_content, no_hover)
end

-- Text and image bytes share the same private staging/transaction path.
function M:save_file_with_conflict_handling(file_name, clipboard_content, no_hover)
	local cwd, err = destination_cwd()
	if not cwd then
		warn("%s", err)
		return false
	end
	if windows and type(file_name) == "string" then
		file_name = file_name:gsub("\\", "/")
	end
	if type(file_name) ~= "string" or file_name == "" or file_name:sub(1, 1) == "/"
		or file_name:sub(-1) == "/" or file_name:find("\0", 1, true)
	then
		warn("Invalid file name: %s", tostring(file_name))
		return false
	end
	local target = cwd
	for component in file_name:gmatch("[^/]+") do
		if component == "." or component == ".." then
			warn("Dot path components are not allowed")
			return false
		end
		if windows and not windows_component(component) then
			warn("Invalid Windows file name component")
			return false
		end
		target = target:join(component)
	end
	local target_safe, target_err = destination_safe(target)
	if not target_safe then
		warn("%s", target_err)
		return false
	end
	local cha, stat_err = transfer_stat(target)
	if stat_err then
		warn("%s", stat_err)
		return false
	end
	local action = "overwrite"
	if cha then
		local choices = {
			{ on = "o", desc = "Overwrite", action = "overwrite" },
			{ on = "c", desc = "Create copy (_copy)", action = "copy" },
			{ on = "q", desc = "Cancel", action = "cancel" },
		}
		local candidates = {}
		for i, choice in ipairs(choices) do
			candidates[i] = { on = choice.on, desc = choice.desc }
		end
		local selected = ya.which({ cands = candidates })
		action = selected and choices[selected].action or "cancel"
		if action == "cancel" then
			return nil
		elseif action == "copy" then
			local copy, copy_err = copy_destination(target, "file")
			if not copy then
				warn("%s", copy_err)
				return false
			end
			target, cha = copy, nil
		else
			local final, final_cha, final_err = file_target(target, target.name)
			if not final then
				warn("%s", final_err)
				return false
			end
			target, cha = final, final_cha
		end
	end
	-- Canonicalize before creating directories; missing parents are resolved
	-- through their nearest existing ancestor, never unchecked string paths.
	local canonical, canonical_err = canonical_leaf(target, cha)
	if not canonical then
		warn("%s", canonical_err)
		return false
	end
	local made, make_err = fs.create("dir_all", target.parent)
	if not made then
		warn("Cannot create destination parent: %s", tostring(make_err))
		return false
	end
	local mode = "0600"
	if cha then
		mode, err = transfer_mode(cha, target)
		if not mode then
			warn("%s", err)
			return false
		end
	end
	local tree = {
		kind = "data",
		data = clipboard_content,
		mode = mode,
	}
	local final, install_err = install_tree(tree, target, cha)
	if not final then
		warn("Failed to save file: %s", install_err)
		return false
	end
	reveal_transfer(final, no_hover)
	return true
end

--==============================================================================
-- Paste Text To File BEGIN
--==============================================================================

--==============================================================================
-- Paste Entry BEGIN
--==============================================================================

-- get list of yanked files and if they are cut
local yanked_info = ya.sync(function()
	local paths = {}
	for _, u in pairs(cx.yanked) do
		paths[#paths + 1] = tostring(u.url)
	end

	return paths, cx.yanked.is_cut
end)

local function single_paste_result(ok)
	return {
		pasted = ok == true and 1 or 0,
		skipped = ok == nil and 1 or 0,
		failed = ok == false and 1 or 0,
		cancelled = ok == nil,
	}
end

local function finish_paste(outcome, show_notify, original_yank)
	if outcome.pasted > 0 and outcome.skipped == 0 and outcome.failed == 0 and not outcome.cancelled then
		-- A prompt may outlive the original selection. Never clear a newer yank.
		local current, is_cut = yanked_info()
		local unchanged = not is_cut and #current == #original_yank
		for i, path in ipairs(current) do
			if path ~= original_yank[i] then
				unchanged = false
				break
			end
		end
		if unchanged then
			ya.emit("unyank", {})
		end
	end
	if show_notify then
		ya.notify({
			title = PackageName,
			content = string.format("%d pasted, %d skipped, %d failed%s",
				outcome.pasted, outcome.skipped, outcome.failed, outcome.cancelled and " (cancelled)" or ""),
			timeout = 5,
			level = outcome.failed > 0 and "warn" or "info",
		})
	end
	return outcome
end

-- Check if mimetypes array contains a mimetype that contains the substring
local function has_mimetype(mimetypes, substring)
	if not mimetypes then
		return false
	end
	for _, mimetype in ipairs(mimetypes) do
		if mimetype:find(substring, 1, true) then
			return true
		end
	end
	return false
end

function M:paste_entry(job)
	local args = job.args or {}
	local no_hover = args.no_hover or false
	local show_notify = false
	for _, arg in ipairs(args) do
		show_notify = show_notify or arg == "notify"
	end
	local yanked_files, is_cut = yanked_info()
	if is_cut then
		ya.emit("paste", {})
		return
	end

	local mimetypes, backend = get_clipboard_mimetypes()
	if mimetypes then
		if has_mimetype(mimetypes, "code/file-list") or has_mimetype(mimetypes, "text/uri-list") then
			local paths = get_clipboard_file_uris(mimetypes, backend)
			if paths then
				return finish_paste(M:handle_file_list_paste(paths, no_hover), show_notify, yanked_files)
			end
			-- A failed file representation may still have a usable image or text.
		end
		if has_mimetype(mimetypes, "image/") then
			return finish_paste(single_paste_result(
				M:handle_image_paste(table.concat(mimetypes, "\n"), no_hover, backend)), show_notify, yanked_files)
		end
		for _, mime in ipairs(mimetypes) do
			if mime:match("^text/plain") or mime == "UTF8_STRING" or mime == "TEXT" or mime == "STRING" then
				local content = read_clipboard(backend == "mac" and "text/plain" or mime, backend)
				if content and content ~= "" then
					return finish_paste(single_paste_result(
						M:handle_text_paste(content, no_hover)), show_notify, yanked_files)
				end
			end
		end
	end

	if #yanked_files > 0 then
		ya.emit("paste", {})
		return
	end
	warn("Clipboard does not contain readable supported data")
end

--==============================================================================
-- Paste Entry END
--==============================================================================

return M
