"""X2Real EX001 camera profiles; retain authored extrinsics and wrist lenses."""
from pxr import Gf, Sdf

PATHS = {
    'left': '/World/Robot/left_arm_gripper_camera_color_frame/Left_Gripper_Camera',
    'right': '/World/Robot/right_arm_gripper_camera_color_frame/Right_Gripper_Camera',
    'head': '/World/Robot/camera_head_front_color_optical_frame/Head_Front_Color_Camera',
}
HEAD_INTRINSICS = dict(fx=720.3193, fy=719.8599, cx=640.3909, cy=351.073)
HEAD_D = [-0.01953918673098087, 0.00478989025577903,
          0.0005033669294789433, -0.00021549213852267712, 0., 0., 0., 0.]


def configure_cameras(stage, camera_class):
    cameras, calibration = {}, {}
    for name, path in PATHS.items():
        prim = stage.GetPrimAtPath(path)
        if not prim or not prim.IsActive():
            raise RuntimeError('Missing native camera: '+path)
        width, height = (1280, 720) if name == 'head' else (640, 480)
        # Upstream overrides head calibration but leaves wrist intrinsics in USD.
        model = 'opencvPinhole' if name == 'head' else 'opencvFisheye'
        prefix = 'omni:lensdistortion:'+model+':'
        if name == 'head':
            prim.ApplyAPI('OmniLensDistortionOpenCvPinholeAPI')
            prim.CreateAttribute('omni:lensdistortion:model', Sdf.ValueTypeNames.Token).Set(model)
            prim.CreateAttribute(prefix+'imageSize', Sdf.ValueTypeNames.Int2).Set(Gf.Vec2i(width,height))
            for field, value in HEAD_INTRINSICS.items():
                prim.CreateAttribute(prefix+field, Sdf.ValueTypeNames.Float).Set(value)
            for field, value in zip(('k1','k2','p1','p2','k3','k4','k5','k6'), HEAD_D):
                prim.CreateAttribute(prefix+field, Sdf.ValueTypeNames.Float).Set(value)
        else:
            # Fail rather than silently substitute a pinhole for native fisheye.
            if prim.GetAttribute('omni:lensdistortion:model').Get() != model:
                raise RuntimeError('Unexpected wrist lens model at '+path)
        native_size = prim.GetAttribute(prefix+'imageSize').Get()
        sx, sy = width/native_size[0], height/native_size[1]
        values = {key: float(prim.GetAttribute(prefix+key).Get()) for key in ('fx','fy','cx','cy')}
        k = [values['fx']*sx, 0., values['cx']*sx, 0., values['fy']*sy, values['cy']*sy, 0.,0.,1.]
        coeffs = ('k1','k2','p1','p2','k3') if name == 'head' else ('k1','k2','k3','k4')
        d = [float(prim.GetAttribute(prefix+field).Get() or 0.) for field in coeffs]
        calibration[name] = dict(width=width,height=height,k=k,d=d,
            distortion_model='plumb_bob' if name == 'head' else 'equidistant',
            native_lens_model=model, native_image_size=list(native_size), prim_path=path,
            nominal_rate_hz=30.)
        # Omit pose args: native camera transform must remain untouched.
        optical = {field: prim.GetAttribute(field).Get() for field in
                   ('focalLength','horizontalAperture','verticalAperture','horizontalApertureOffset','verticalApertureOffset')}
        cameras[name] = camera_class(path, name=name+'_camera', frequency=30, resolution=(width,height))
        # Camera's generic square-pixel setup rewrites aperture; upstream lens
        # distortion uses authored optics. Restore them after constructing the wrapper.
        for field, value in optical.items():
            prim.GetAttribute(field).Set(value)
    return cameras, calibration
