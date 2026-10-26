# DDS template - HyperView Tcl (hwi/post API): load OptiStruct .h3d, von Mises contour, save PNG
# Run   : HyperWorks batch, e.g.  hw.exe -b -tcl hv_contour_png.tcl   (cwd = case dir)
#         (contract key "altair_hv": {altair_hvp} {script}; check which executable your install provides)
#         HyperWorks 2023+ (new UI) may require runhwx.exe + Python instead.   TODO(사내검증)
# In    : RESULT_FILE (default case.h3d written by OptiStruct)
# Out   : images/mises.png, images/disp.png  (no results.json - numbers come from os_case_driver.py)
#
# Data type / component strings depend on the result file - open it once in HyperView and read the
# names in the Contour panel. Batch screen capture depends on graphics drivers; Altair recommends
# GUI-mode runs for images if batch output is blank or small.   TODO(사내검증)
# Save as UTF-8. If HyperView reports encoding errors, remove non-ASCII characters from comments.

set RESULT_FILE "case.h3d"
set PLOTS [list \
    [list mises.png "Element Stresses (2D & 3D)" "vonMises"] \
    [list disp.png  "Displacement"               "Mag"] ]
set IMG_W 1600
set IMG_H 1000

set case_dir [pwd]
set img_dir [file join $case_dir images]
file mkdir $img_dir
set h3d [file join $case_dir $RESULT_FILE]
if {![file exists $h3d]} { puts "\[DDS\] result not found: $h3d"; exit 1 }

hwi OpenStack
hwi GetSessionHandle sess
sess GetProjectHandle proj
proj GetPageHandle page [proj GetActivePage]
page GetWindowHandle win [page GetActiveWindow]
win SetClientType "Animation"
win GetClientHandle post

set mid [post AddModel $h3d]
post GetModelHandle mdl $mid
mdl SetResult $h3d
mdl GetResultCtrlHandle res
res GetContourCtrlHandle cnt

foreach p $PLOTS {
    lassign $p fname dtype comp
    cnt SetDataType $dtype
    cnt SetDataComponent $comp
    cnt SetEnableState true
    post SetDisplayOptions "contour" true
    post Draw
    # view orientation/fit - TODO(사내검증): method names of the view control handle
    if {![catch {win GetViewControlHandle view}]} {
        catch {view SetOrientation iso}
        catch {view Fit}
        catch {view ReleaseHandle}
    }
    set png [file join $img_dir $fname]
    # TODO(사내검증): CaptureScreen signature; alternative 'sess CaptureScreenToSize png <file> <w> <h>'
    if {[catch {sess CaptureScreen png $png} err]} {
        if {[catch {sess CaptureScreenToSize png $png $IMG_W $IMG_H} err2]} {
            puts "\[DDS\] capture failed: $err / $err2"
        }
    }
    puts "\[DDS\] image: $png"
}

hwi CloseStack
