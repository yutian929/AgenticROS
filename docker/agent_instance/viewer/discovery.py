"""Build RViz views from public, standard ROS topics; no device packages or scene data."""
import re


def robots(roster, instance_id):
    if roster.get('instance_id') != instance_id:
        raise ValueError('Registry identity changed; refusing another Agent instance')
    result = {}
    for item in roster.get('robots', []):
        robot, session, namespace = item['robot_id'], item['session_id'], item['namespace']
        if (not re.fullmatch('[a-z][a-z0-9_]{0,47}', robot)
                or not re.fullmatch('[0-9a-f]{12,32}', session)
                or namespace != f'/robots/{robot}/s_{session}'):
            raise ValueError('Invalid robot identity')
        if item.get('connected'):
            result[robot] = namespace
    return result


def scoped_topics(topics, namespace):
    return sorted((name, types) for name, types in topics if name.startswith(namespace+'/'))


def fixed_frame(namespace, transforms, sensor_frames):
    edges = [(parent, child) for parent, child in transforms
             if parent.startswith(namespace.strip('/')+'/') and child.startswith(namespace.strip('/')+'/')]
    roots = sorted({p for p, _ in edges} - {c for _, c in edges})
    frames = sorted(f for f in sensor_frames if f.startswith(namespace.strip('/')+'/'))
    return next(iter(roots or frames), '')


def configuration(topics, frame):
    displays = [dict(Class='rviz_default_plugins/Grid', Name='Grid', Enabled=True,
                     **{'Cell Size': 0.1, 'Plane Cell Count': 20}),
                dict(Class='rviz_default_plugins/TF', Name='TF', Enabled=True,
                     **{'Show Names': False, 'Marker Scale': 0.1})]
    image_enabled = False
    for name, types in topics:
        topic = {'Value': name, 'Reliability Policy': 'Best Effort', 'Durability Policy': 'Volatile',
                 'History Policy': 'Keep Last', 'Depth': 1}
        if 'sensor_msgs/msg/Image' in types or 'sensor_msgs/msg/CompressedImage' in types:
            compressed = 'sensor_msgs/msg/CompressedImage' in types and name.endswith('/compressed')
            if 'sensor_msgs/msg/Image' in types and any(n == name+'/compressed' for n, _ in topics):
                continue
            displays.append(dict(Class='rviz_default_plugins/Image', Name=name, Enabled=compressed and not image_enabled,
                                 Topic=topic, **{'Normalize Range': True}))
            image_enabled = image_enabled or compressed
        elif 'sensor_msgs/msg/PointCloud2' in types:
            displays.append(dict(Class='rviz_default_plugins/PointCloud2', Name=name, Enabled=True,
                                 Topic=topic, **{'Queue Size': 1, 'Style': 'Flat Squares', 'Size (m)': 0.008,
                                                'Color Transformer': 'RGB8', 'Position Transformer': 'XYZ'}))
        elif name.endswith('/robot_description') and 'std_msgs/msg/String' in types:
            topic.update({'Reliability Policy': 'Reliable', 'Durability Policy': 'Transient Local'})
            displays.append(dict(Class='rviz_default_plugins/RobotModel', Name='Robot model', Enabled=True,
                                 **{'Description Source': 'Topic', 'Description Topic': topic}))
    return {'Panels': [{'Class': 'rviz_common/Displays', 'Name': 'Displays'},
                       {'Class': 'rviz_common/Views', 'Name': 'Views'}],
            'Visualization Manager': {'Class': '', 'Global Options': {'Fixed Frame': frame, 'Frame Rate': 10,
                'Background Color': '28; 34; 43'}, 'Displays': displays,
                'Tools': [{'Class': 'rviz_default_plugins/'+tool} for tool in ('Interact','MoveCamera','Select','FocusCamera')],
                'Views': {'Current': {'Class': 'rviz_default_plugins/Orbit', 'Target Frame': frame,
                                     'Distance': 1.5, 'Pitch': 0.65, 'Yaw': 5.1,
                                     'Focal Point': {'X': 0, 'Y': 0, 'Z': 0.1}}}},
            'Window Geometry': {'Width': 1440, 'Height': 740, 'X': 0, 'Y': 140}}
