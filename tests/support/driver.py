"""Plugin driver shared by the X11 and opt-in native Windows harnesses."""

DRIVER = r'''
local ucp = require("ucp")
local M = {}
local probe = ya.sync(function()
    local tab = cx.active
    local selected, yanked = 0, 0
    for _ in pairs(tab.selected) do selected = selected + 1 end
    for _ in pairs(cx.yanked) do yanked = yanked + 1 end
    return table.concat({tostring(tab.current.cwd),
        tab.current.hovered and tostring(tab.current.hovered.url) or "",
        tostring(selected), tostring(yanked), tostring(cx.yanked.is_cut)}, "\0")
end)
function M:entry(job)
    local action = job.args[1]
    if action == "probe" then
        assert(fs.write(Url(os.getenv("UCP_PROBE")), probe()))
        return
    end
    if action == "select-virtual" then
        -- Real URL/File/selection objects, without connecting to an SFTP server.
        local source = Url(os.getenv("UCP_VIRTUAL_SOURCE"))
        local file = File({
            url = Url("sftp://ucp-test/" .. tostring(source.path)),
            cha = assert(fs.cha(source, false)),
        })
        ya.emit("toggle", {file, state = "on"})
        return
    end
    local result = ucp:entry(job)
    if type(result) == "table" then
        result = table.concat({result.pasted, result.skipped, result.failed, tostring(result.cancelled)}, "\0")
    end
    if type(result) == "string" then
        assert(fs.write(Url(os.getenv("UCP_RESULT")), result))
    end
    assert(fs.write(Url(os.getenv("UCP_DONE")), action))
end
return M
'''
