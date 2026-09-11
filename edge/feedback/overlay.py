CONNECTIONS = [('left_shoulder','right_shoulder'),('left_shoulder','left_elbow'),
               ('left_elbow','left_wrist'),('right_shoulder','right_elbow'),('right_elbow','right_wrist'),
               ('left_shoulder','left_hip'),('right_shoulder','right_hip'),('left_hip','right_hip')]


def draw_overlay(rgb, pose, telemetry):
    import cv2
    image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    h, w = image.shape[:2]
    visible = {name:(round(p.x*(w-1)),round(p.y*(h-1))) for name,p in pose.landmarks.items()
               if p.confidence >= .65 and 0 <= p.x < 1 and 0 <= p.y < 1}
    for a,b in CONNECTIONS:
        if a in visible and b in visible:
            cv2.line(image,visible[a],visible[b],(100,220,100),2)
    for p in visible.values():
        cv2.circle(image,p,4,(40,240,240),-1)
    angle = telemetry.get('angle')
    lines = ['RehabAI | LIVE RGB-D | research prototype',
             f"Angle: {angle:.1f} deg" if angle is not None else 'Angle: INVALID / CALIBRATING',
             f"Reps: {telemetry['reps']} | {telemetry['safety']} | Confidence: {telemetry['confidence']:.2f}",
             ' '.join(f'{k}: {"OK" if v else "NO"}' for k,v in telemetry.get('checks',{}).items()),
             telemetry['feedback'], 'Q: stop    C: recalibrate']
    for i,line in enumerate(lines):
        cv2.putText(image,line,(10,24+i*23),cv2.FONT_HERSHEY_SIMPLEX,.45,(0,0,0),3)
        cv2.putText(image,line,(10,24+i*23),cv2.FONT_HERSHEY_SIMPLEX,.45,(255,255,255),1)
    return image
