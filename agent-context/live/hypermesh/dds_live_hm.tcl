# DDS Live Link — HyperMesh (Tcl)
# HyperMesh GUI 안에서 127.0.0.1:47802 로 에이전트 요청을 받아 Tcl 코드를 GUI 이벤트 루프(메인 스레드)에서 실행한다.
# 사용: HyperMesh 실행 후 Tcl 콘솔(또는 File > Run > Tcl/Tk Script)에서  source dds_live_hm.tcl
#       항상 켜려면 hmcustom.tcl 에 source 한 줄을 추가한다.
# 프로토콜: ../PROTOCOL.md  (Tcl은 JSON 파서가 없어 code는 base64(code_b64)로 받는다)
# TODO(사내검증): HyperMesh 버전의 Tcl이 8.6 미만이면 `binary decode base64`가 없다 → tcllib base64 사용(아래 decode64 참고).
# TODO(사내검증): 화면 캡처 명령(*writejpegfile / hm_screengrab / hwi 캡처)이 버전마다 다르다 — 실패하면 dds-live가 창 캡처로 대신한다.

namespace eval ::ddslive {
    variable port 47802
    variable token ""
    variable server ""
}

proc ::ddslive::loadToken {} {
    if {[info exists ::env(DDS_LIVE_TOKEN)] && $::env(DDS_LIVE_TOKEN) ne ""} { return $::env(DDS_LIVE_TOKEN) }
    set f [file join $::env(USERPROFILE) .dds-agent live.token]
    if {[file exists $f]} {
        set fh [open $f r]; set t [string trim [read $fh]]; close $fh
        return $t
    }
    return ""
}

proc ::ddslive::decode64 {s} {
    if {![catch {binary decode base64 $s} r]} { return [encoding convertfrom utf-8 $r] }
    package require base64
    return [encoding convertfrom utf-8 [::base64::decode $s]]
}

# 아주 단순한 JSON 문자열 필드 추출 (요청은 dds-live가 만들므로 형식이 고정돼 있다)
proc ::ddslive::field {json key} {
    if {[regexp "\"$key\"\\s*:\\s*\"(\[^\"\]*)\"" $json -> v]} { return $v }
    if {[regexp "\"$key\"\\s*:\\s*(\[0-9\]+)" $json -> v]} { return $v }
    return ""
}

proc ::ddslive::esc {s} {
    return [string map [list "\\" "\\\\" "\"" "\\\"" "\n" "\\n" "\r" "\\r" "\t" "\\t"] $s]
}

proc ::ddslive::reply {chan id ok fields} {
    set out "{\"id\": [expr {$id eq "" ? "null" : $id}], \"ok\": [expr {$ok ? "true" : "false"}]"
    foreach {k v} $fields { append out ", \"$k\": \"[::ddslive::esc $v]\"" }
    append out "}"
    puts $chan [encoding convertto utf-8 $out]
    flush $chan
}

proc ::ddslive::onRead {chan} {
    if {[eof $chan] || [catch {gets $chan line} n] || $n < 0} {
        catch {close $chan}
        return
    }
    if {[string trim $line] eq ""} { return }
    set line [encoding convertfrom utf-8 $line]
    set id [::ddslive::field $line id]
    if {$::ddslive::token ne "" && [::ddslive::field $line token] ne $::ddslive::token} {
        ::ddslive::reply $chan $id 0 [list error "토큰이 맞지 않습니다"]
        return
    }
    switch -- [::ddslive::field $line op] {
        ping {
            ::ddslive::reply $chan $id 1 [list tool hypermesh version [info patchlevel] pid [pid]]
        }
        info {
            # 현재 모델 요약: 컴포넌트·프로퍼티·재료 수 (TODO(사내검증): hm_count 인자)
            set summary ""
            foreach ent {comps props mats elems nodes} {
                catch { append summary "$ent=[hm_count $ent all 0] " }
            }
            ::ddslive::reply $chan $id 1 [list model [hm_info currentfile] summary $summary]
        }
        exec {
            set code [::ddslive::decode64 [::ddslive::field $line code_b64]]
            # GUI 이벤트 루프 안이므로 메인 스레드에서 실행된다. 화면은 실행 후 갱신된다.
            if {[catch {uplevel #0 $code} res]} {
                ::ddslive::reply $chan $id 0 [list error $res]
            } else {
                catch { *redraw }
                ::ddslive::reply $chan $id 1 [list result $res stdout ""]
            }
        }
        screenshot {
            set path [::ddslive::decode64 [::ddslive::field $line path_b64]]
            # TODO(사내검증): 아래 명령은 버전에 따라 다르다. 실패하면 오류를 돌려주고 dds-live가 창 캡처로 대신한다.
            if {[catch { hm_screencapture png $path } err]} {
                ::ddslive::reply $chan $id 0 [list error "화면 캡처 명령 실패: $err"]
            } else {
                ::ddslive::reply $chan $id 1 [list path $path]
            }
        }
        default {
            ::ddslive::reply $chan $id 0 [list error "알 수 없는 op"]
        }
    }
}

proc ::ddslive::onAccept {chan addr port} {
    if {$addr ne "127.0.0.1"} { close $chan; return }
    fconfigure $chan -buffering line -translation lf -encoding binary
    fileevent $chan readable [list ::ddslive::onRead $chan]
}

proc ::ddslive::start {} {
    set ::ddslive::token [::ddslive::loadToken]
    if {$::ddslive::server ne ""} { catch {close $::ddslive::server} }
    set ::ddslive::server [socket -server ::ddslive::onAccept -myaddr 127.0.0.1 $::ddslive::port]
    puts "DDS Live Link: HyperMesh listening on 127.0.0.1:$::ddslive::port"
}

::ddslive::start
