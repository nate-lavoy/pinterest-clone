DELETE FROM Pin
WHERE board_id NOT IN (SELECT board_id FROM Pinboard);

DELETE FROM Pin
WHERE pinned_by NOT IN (SELECT user_id FROM AppUser);

DELETE FROM Pin
WHERE picture_id NOT IN (SELECT picture_id FROM Picture);

SELECT conname
FROM pg_constraint
WHERE conrelid = 'comment'::regclass AND contype = 'f' AND conkey = ARRAY[
  (SELECT attnum FROM pg_attribute
   WHERE attrelid = 'comment'::regclass AND attname = 'pin_id')
];

ALTER TABLE Comment
DROP CONSTRAINT comment_pin_id_fkey,
ADD CONSTRAINT comment_pin_id_fkey
FOREIGN KEY (pin_id) REFERENCES Pin(pin_id) ON DELETE CASCADE;

ALTER TABLE Pin
DROP CONSTRAINT pin_board_id_fkey,
ADD CONSTRAINT pin_board_id_fkey
FOREIGN KEY (board_id) REFERENCES Pinboard(board_id) ON DELETE CASCADE;

ALTER TABLE FollowedBoard
DROP CONSTRAINT followedboard_stream_id_fkey,
ADD CONSTRAINT followedboard_stream_id_fkey
FOREIGN KEY (stream_id) REFERENCES FollowStream(stream_id) ON DELETE CASCADE;

TRUNCATE TABLE Comment, PictureLike, FollowedBoard, Friendship, Pin, Picture, Pinboard, AppUser, FollowStream  RESTART IDENTITY CASCADE;
